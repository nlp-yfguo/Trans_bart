#encoding: utf-8

import sys
import torch
from torch.nn import ModuleList

from parallel.parallelMT import DataParallelMT
from transformer.EnsembleNMT import NMT as Ensemble
from transformer.NMT import NMT
from utils.base import set_random_seed
from utils.fmt.base import sys_open
from utils.fmt.base4torch import parse_cuda_decode
from utils.fmt.vocab.base import reverse_dict
from utils.fmt.vocab.token import ldvocab
from utils.h5serial import h5File
from utils.io import load_model_cpu
from utils.torch.comp import torch_compile, torch_inference_mode
from utils.tqdm import tqdm

import cnfg.base as cnfg
from cnfg.ihyp import *
from cnfg.vocab.base import eos_id

def load_fixing(module):

	if hasattr(module, "fix_load"):
		module.fix_load()

td = h5File(cnfg.test_data, "r")

ntest = td["ndata"][()].item()
nwordi = td["nword"][()].tolist()[0]
vcbt, nwordt = ldvocab(sys.argv[2])
vcbt = reverse_dict(vcbt)

if len(sys.argv) == 4:
	mymodel = NMT(cnfg.isize, nwordi, nwordt, cnfg.nlayer, cnfg.ff_hsize, cnfg.drop, cnfg.attn_drop, cnfg.share_emb, cnfg.nhead, cache_len_default, cnfg.attn_hsize, cnfg.norm_output, cnfg.bindDecoderEmb, cnfg.forbidden_indexes)

	mymodel = load_model_cpu(sys.argv[3], mymodel)
	mymodel.apply(load_fixing)

else:
	models = []
	for modelf in sys.argv[3:]:
		tmp = NMT(cnfg.isize, nwordi, nwordt, cnfg.nlayer, cnfg.ff_hsize, cnfg.drop, cnfg.attn_drop, cnfg.share_emb, cnfg.nhead, cache_len_default, cnfg.attn_hsize, cnfg.norm_output, cnfg.bindDecoderEmb, cnfg.forbidden_indexes)

		tmp = load_model_cpu(modelf, tmp)
		tmp.apply(load_fixing)

		models.append(tmp)
	mymodel = Ensemble(models)

mymodel.eval()

use_cuda, cuda_device, cuda_devices, multi_gpu = parse_cuda_decode(cnfg.use_cuda, cnfg.gpuid, cnfg.multi_gpu_decoding)

# Important to make cudnn methods deterministic
set_random_seed(cnfg.seed, use_cuda)

if cuda_device:
	mymodel.to(cuda_device, non_blocking=True)
	if multi_gpu:
		mymodel = DataParallelMT(mymodel, device_ids=cuda_devices, output_device=cuda_device.index, host_replicate=True, gather_output=False)

mymodel = torch_compile(mymodel, *torch_compile_args, **torch_compile_kwargs)

beam_size = cnfg.beam_size
length_penalty = cnfg.length_penalty

ens = "\n".encode("utf-8")

fp = sys.argv[1]
_ind = fp.rfind(".")
fpe = fp[:_ind] + "/enc_%d" + fp[_ind:]
fpd = fp[:_ind] + "/dec_%d" + fp[_ind:]
encl = list(mymodel.enc.nets)
decl = list(mymodel.dec.nets)

src_grp = td["src"]
for _cur_r_layer in range(cnfg.nlayer):

	mymodel.enc.nets = ModuleList(encl[:_cur_r_layer] + encl[_cur_r_layer + 1:])
	mymodel.dec.nets = ModuleList(decl)
	with sys_open(fpe % (_cur_r_layer), "wb") as f:
		with torch_inference_mode():
			for i in tqdm(range(ntest), mininterval=tqdm_mininterval):
				seq_batch = torch.from_numpy(src_grp[str(i)][()])
				if cuda_device:
					seq_batch = seq_batch.to(cuda_device, non_blocking=True)
				seq_batch = seq_batch.long()
				output = mymodel.decode(seq_batch, beam_size, None, length_penalty)
				#output = mymodel.train_decode(seq_batch, beam_size, None, length_penalty)
				if multi_gpu:
					tmp = []
					for ou in output:
						tmp.extend(ou.tolist())
					output = tmp
				else:
					output = output.tolist()
				for tran in output:
					tmp = []
					for tmpu in tran:
						if tmpu == eos_id:
							break
						else:
							tmp.append(vcbt[tmpu])
					f.write(" ".join(tmp).encode("utf-8"))
					f.write(ens)

	mymodel.enc.nets = ModuleList(encl)
	mymodel.dec.nets = ModuleList(decl[:_cur_r_layer] + decl[_cur_r_layer + 1:])
	with sys_open(fpd % (_cur_r_layer), "wb") as f:
		with torch_inference_mode():
			for i in tqdm(range(ntest), mininterval=tqdm_mininterval):
				seq_batch = torch.from_numpy(src_grp[str(i)][()])
				if cuda_device:
					seq_batch = seq_batch.to(cuda_device, non_blocking=True)
				seq_batch = seq_batch.long()
				output = mymodel.decode(seq_batch, beam_size, None, length_penalty)
				#output = mymodel.train_decode(seq_batch, beam_size, None, length_penalty)
				if multi_gpu:
					tmp = []
					for ou in output:
						tmp.extend(ou.tolist())
					output = tmp
				else:
					output = output.tolist()
				for tran in output:
					tmp = []
					for tmpu in tran:
						if tmpu == eos_id:
							break
						else:
							tmp.append(vcbt[tmpu])
					f.write(" ".join(tmp).encode("utf-8"))
					f.write(ens)

td.close()
