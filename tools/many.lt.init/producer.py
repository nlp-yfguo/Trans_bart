#encoding: utf-8

import torch
from torch.optim import Adam as Optimizer

from loss.base import LabelSmoothingLoss
from lrsch import GoogleLR as LRScheduler
from parallel.base import DataParallelCriterion
from parallel.optm import MultiGPUGradScaler
from parallel.parallelMT import DataParallelMT
from transformer.NMT import NMT
from utils.base import free_cache, get_logger, mkdir, set_random_seed
from utils.contpara import get_model_parameters
from utils.data import inf_data_generator
from utils.fmt.base import save_objects
from utils.fmt.base4torch import load_emb, parse_cuda
from utils.h5serial import h5File
from utils.init.base import init_model_params
from utils.io import load_model_cpu, save_model
from utils.torch.comp import torch_autocast, torch_compile, torch_inference_mode
from utils.tqdm import tqdm
from utils.train.base import optm_step, optm_step_zero_grad_set_none

import cnfg.base as cnfg
from cnfg.ihyp import *
from cnfg.vocab.base import pad_id

def train(td, tl, ed, nd, optm, lrsch, model, lossf, mv_device, logger, multi_gpu, multi_gpu_optimizer, tokens_optm=32768, nreport=None, report_eva=True, remain_steps=None, scaler=None):

	sum_loss = part_loss = 0.0
	sum_wd = part_wd = 0
	_use_amp = scaler is not None
	model.train()
	cur_b, cur_step, _done_tokens = 1, 1, 0
	src_grp, tgt_grp = td["src"], td["tgt"]
	for i_d in tqdm(tl, mininterval=tqdm_mininterval):
		seq_batch = torch.from_numpy(src_grp[i_d][()])
		seq_o = torch.from_numpy(tgt_grp[i_d][()])
		lo = seq_o.size(1) - 1
		if mv_device:
			seq_batch = seq_batch.to(mv_device, non_blocking=True)
			seq_o = seq_o.to(mv_device, non_blocking=True)
		seq_batch, seq_o = seq_batch.long(), seq_o.long()

		oi = seq_o.narrow(1, 0, lo)
		ot = seq_o.narrow(1, 1, lo).contiguous()
		with torch_autocast(enabled=_use_amp):
			output = model(seq_batch, oi)
			loss = lossf(output, ot)
			if multi_gpu:
				loss = loss.sum()
		loss_add = loss.data.item()

		if scaler is None:
			loss.backward()
		else:
			scaler.scale(loss).backward()

		wd_add = ot.ne(pad_id).int().sum().item()
		loss = output = oi = ot = seq_batch = seq_o = None
		sum_loss += loss_add
		sum_wd += wd_add
		_done_tokens += wd_add

		if _done_tokens >= tokens_optm:
			optm_step(optm, model=model, scaler=scaler, multi_gpu=multi_gpu, multi_gpu_optimizer=multi_gpu_optimizer, zero_grad_none=optm_step_zero_grad_set_none)
			_done_tokens = 0
			if cur_step >= remain_steps:
				break
			cur_step += 1
			lrsch.step()

		if nreport is not None:
			part_loss += loss_add
			part_wd += wd_add
			if cur_b % nreport == 0:
				if report_eva:
					_leva, _eeva = eva(ed, nd, model, lossf, mv_device, multi_gpu, _use_amp)
					logger.info("Average loss over %d tokens: %.3f, valid loss/error: %.3f %.2f" % (part_wd, part_loss / part_wd, _leva, _eeva,))
					free_cache(mv_device)
					model.train()
				else:
					logger.info("Average loss over %d tokens: %.3f" % (part_wd, part_loss / part_wd,))
				part_loss = 0.0
				part_wd = 0
		cur_b += 1

	return sum_loss / sum_wd

def eva(ed, nd, model, lossf, mv_device, multi_gpu, use_amp=False):
	r = w = 0
	sum_loss = 0.0
	model.eval()
	src_grp, tgt_grp = ed["src"], ed["tgt"]
	with torch_inference_mode():
		for i in tqdm(range(nd), mininterval=tqdm_mininterval):
			bid = str(i)
			seq_batch = torch.from_numpy(src_grp[bid][()])
			seq_o = torch.from_numpy(tgt_grp[bid][()])
			lo = seq_o.size(1) - 1
			if mv_device:
				seq_batch = seq_batch.to(mv_device, non_blocking=True)
				seq_o = seq_o.to(mv_device, non_blocking=True)
			seq_batch, seq_o = seq_batch.long(), seq_o.long()
			ot = seq_o.narrow(1, 1, lo).contiguous()
			with torch_autocast(enabled=use_amp):
				output = model(seq_batch, seq_o.narrow(1, 0, lo))
				loss = lossf(output, ot)
				if multi_gpu:
					loss = loss.sum()
					trans = torch.cat([outu.argmax(-1).to(mv_device, non_blocking=True) for outu in output], 0)
				else:
					trans = output.argmax(-1)
			sum_loss += loss.data.item()
			data_mask = ot.ne(pad_id)
			correct = (trans.eq(ot) & data_mask).int()
			w += data_mask.int().sum().item()
			r += correct.sum().item()
			correct = data_mask = trans = loss = output = ot = seq_batch = seq_o = None
	w = float(w)
	return sum_loss / w, (w - r) / w * 100.0

def init_fixing(module):

	if hasattr(module, "fix_init"):
		module.fix_init()

def load_fixing(module):

	if hasattr(module, "fix_load"):
		module.fix_load()

rid = cnfg.run_id
maxrun = cnfg.maxrun
tokens_optm = cnfg.tokens_optm
done_tokens = 0
batch_report = cnfg.batch_report
report_eva = cnfg.report_eva
use_ams = cnfg.use_ams
remain_steps = cnfg.training_steps

wkdir = "".join((cnfg.exp_dir, cnfg.data_id, "/", cnfg.group_id, "/", rid, "/"))
mkdir(wkdir)

logger = get_logger(wkdir + "train.log")

use_cuda, cuda_device, cuda_devices, multi_gpu = parse_cuda(cnfg.use_cuda, cnfg.gpuid)
multi_gpu_optimizer = multi_gpu and cnfg.multi_gpu_optimizer

set_random_seed(cnfg.seed, use_cuda)

td = h5File(cnfg.train_data, "r")
vd = h5File(cnfg.dev_data, "r")

ntrain = td["ndata"][()].item()
nvalid = vd["ndata"][()].item()
nword = td["nword"][()].tolist()
nwordi, nwordt = nword[0], nword[-1]

tl = inf_data_generator([str(i) for i in range(ntrain)], shuf=True)

logger.info("Design models with seed: %d" % torch.initial_seed())
mymodel = NMT(cnfg.isize, nwordi, nwordt, cnfg.nlayer, cnfg.ff_hsize, cnfg.drop, cnfg.attn_drop, cnfg.act_drop, cnfg.share_emb, cnfg.nhead, cache_len_default, cnfg.attn_hsize, cnfg.norm_output, cnfg.bindDecoderEmb, cnfg.forbidden_indexes)

fine_tune_m = cnfg.fine_tune_m

mymodel = init_model_params(mymodel)
mymodel.apply(init_fixing)
if fine_tune_m is not None:
	logger.info("Load pre-trained model from: " + fine_tune_m)
	mymodel = load_model_cpu(fine_tune_m, mymodel)
	mymodel.apply(load_fixing)

lossf = LabelSmoothingLoss(nwordt, cnfg.label_smoothing, ignore_index=pad_id, reduction="sum", forbidden_index=cnfg.forbidden_indexes)

if cnfg.src_emb is not None:
	logger.info("Load source embedding from: " + cnfg.src_emb)
	load_emb(cnfg.src_emb, mymodel.enc.wemb.weight, nwordi, cnfg.scale_down_emb, cnfg.freeze_srcemb)
if cnfg.tgt_emb is not None:
	logger.info("Load target embedding from: " + cnfg.tgt_emb)
	load_emb(cnfg.tgt_emb, mymodel.dec.wemb.weight, nwordt, cnfg.scale_down_emb, cnfg.freeze_tgtemb)

if cuda_device:
	mymodel.to(cuda_device, non_blocking=True)
	lossf.to(cuda_device, non_blocking=True)

use_amp = cnfg.use_amp and use_cuda
scaler = (MultiGPUGradScaler() if multi_gpu_optimizer else GradScaler()) if use_amp else None

if multi_gpu:
	mymodel = DataParallelMT(mymodel, device_ids=cuda_devices, output_device=cuda_device.index, host_replicate=True, gather_output=False)
	lossf = DataParallelCriterion(lossf, device_ids=cuda_devices, output_device=cuda_device.index, replicate_once=True)

if multi_gpu:
	optimizer = mymodel.build_optimizer(Optimizer, lr=init_lr, betas=adam_betas_default, eps=ieps_adam_default, weight_decay=cnfg.weight_decay, amsgrad=use_ams, multi_gpu_optimizer=multi_gpu_optimizer, contiguous_parameters=contiguous_parameters)
else:
	optimizer = Optimizer(get_model_parameters(mymodel, contiguous_parameters=contiguous_parameters), lr=init_lr, betas=adam_betas_default, eps=ieps_adam_default, weight_decay=cnfg.weight_decay, amsgrad=use_ams)
optimizer.zero_grad(set_to_none=optm_step_zero_grad_set_none)

lrsch = LRScheduler(optimizer, cnfg.isize, cnfg.warm_step, scale=cnfg.lr_scale)

mymodel = torch_compile(mymodel, *torch_compile_args, **torch_compile_kwargs)
lossf = torch_compile(lossf, *torch_compile_args, **torch_compile_kwargs)

perf = {}

for i in range(1, maxrun + 1):
	free_cache(use_cuda)
	init_vloss, init_vprec = eva(vd, nvalid, mymodel, lossf, cuda_device, multi_gpu, use_amp)
	terr = train(td, tl, vd, nvalid, optimizer, lrsch, mymodel, lossf, cuda_device, logger, multi_gpu, multi_gpu_optimizer, tokens_optm, batch_report, report_eva, remain_steps, scaler)
	vloss, vprec = eva(vd, nvalid, mymodel, lossf, cuda_device, multi_gpu, use_amp)
	logger.info("Model: %d, train loss: %.3f, valid loss/error: %.3f %.2f" % (i, terr, vloss, vprec,))

	save_model(mymodel, wkdir + "model_%d_%.3f_%.3f_%.2f.h5" % (i, terr, vloss, vprec,), multi_gpu, print_func=logger.info)
	perf[i] = (terr, vloss, vprec, init_vloss, init_vprec,)

	if multi_gpu:
		mymodel.module = init_model_params(mymodel.module)
		mymodel.module.apply(init_fixing)
		mymodel.update_replicas()
	else:
		mymodel = init_model_params(mymodel)
		mymodel.apply(init_fixing)

	if multi_gpu:
		optimizer = mymodel.build_optimizer(Optimizer, lr=init_lr, betas=adam_betas_default, eps=ieps_adam_default, weight_decay=cnfg.weight_decay, amsgrad=use_ams, multi_gpu_optimizer=multi_gpu_optimizer, contiguous_parameters=contiguous_parameters)
	else:
		optimizer = Optimizer(get_model_parameters(mymodel, contiguous_parameters=contiguous_parameters), lr=init_lr, betas=adam_betas_default, eps=ieps_adam_default, weight_decay=cnfg.weight_decay, amsgrad=use_ams)
	optimizer.zero_grad(set_to_none=optm_step_zero_grad_set_none)

	lrsch = LRScheduler(optimizer, cnfg.isize, cnfg.warm_step, scale=cnfg.lr_scale)

save_objects(wkdir + "perf.txt", perf)

td.close()
vd.close()
