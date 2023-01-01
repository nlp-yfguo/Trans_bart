#encoding: utf-8

import torch
from math import sqrt
from torch import nn

from modules.base import Dropout
from modules.elinear import MBLinear
from modules.mulang.base import LayerNorm
from modules.mulang.o2m import CrossAttn, PositionwiseFF, SelfAttn
from transformer.Decoder import Decoder as DecoderBase, DecoderLayer as DecoderLayerBase
from utils.base import index_tensors, select_zero_
from utils.beam import expand_bsize_for_beam
from utils.sampler import SampleMax
from utils.torch.comp import all_done, torch_no_grad

from cnfg.ihyp import *
from cnfg.vocab.base import eos_id, pad_id

class DecoderLayer(DecoderLayerBase):

	def __init__(self, isize, fhsize=None, dropout=0.0, attn_drop=0.0, num_head=8, ahsize=None, ngroup=None, ntask=None, k_rel_pos=use_k_relative_position_decoder, max_bucket_distance=relative_position_max_bucket_distance_decoder, **kwargs):

		_ahsize = isize if ahsize is None else ahsize
		_fhsize = _ahsize * 4 if fhsize is None else fhsize

		super(DecoderLayer, self).__init__(isize, fhsize=_fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, ahsize=_ahsize, k_rel_pos=k_rel_pos, max_bucket_distance=max_bucket_distance, **kwargs)

		self.self_attn = SelfAttn(isize, _ahsize, isize, ngroup, num_head=num_head, dropout=attn_drop, k_rel_pos=k_rel_pos, uni_direction_reduction=True, max_bucket_distance=max_bucket_distance)
		self.cross_attn = CrossAttn(isize, _ahsize, isize, ngroup, num_head=num_head, dropout=attn_drop)
		self.ff = PositionwiseFF(isize, ngroup, hsize=_fhsize, dropout=dropout, ntask=ntask)
		self.layer_normer1 = LayerNorm(isize, ntask=ntask, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters)
		self.layer_normer2 = LayerNorm(isize, ntask=ntask, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters)

	def forward(self, inpute, inputo, sattn_w=None, cattn_w=None, ffn_w=None, taskid=None, src_pad_mask=None, tgt_pad_mask=None, query_unit=None, **kwargs):

		if query_unit is None:
			_inputo = self.layer_normer1(inputo, taskid=taskid)

			context = self.self_attn(_inputo, mask=tgt_pad_mask, weight=sattn_w)

			if self.drop is not None:
				context = self.drop(context)

			context = context + (_inputo if self.norm_residual else inputo)

		else:
			_query_unit = self.layer_normer1(query_unit, taskid=taskid)

			context, states_return = self.self_attn(_query_unit, states=inputo, weight=sattn_w)

			if self.drop is not None:
				context = self.drop(context)

			context = context + (_query_unit if self.norm_residual else query_unit)

		_context = self.layer_normer2(context, taskid=taskid)
		_context_new = self.cross_attn(_context, inpute, mask=src_pad_mask, weight=cattn_w)

		if self.drop is not None:
			_context_new = self.drop(_context_new)

		context = _context_new + (_context if self.norm_residual else context)

		context = self.ff(context, weight=ffn_w, taskid=taskid)

		if query_unit is None:
			return context
		else:
			return context, states_return

class Decoder(DecoderBase):

	def __init__(self, isize, nwd, num_layer, fhsize=None, dropout=0.0, attn_drop=0.0, emb_w=None, num_head=8, xseql=cache_len_default, ahsize=None, norm_output=True, bindemb=True, forbidden_index=None, ntask=None, ngroup=None, task_emb_w=None, share_layer=False, **kwargs):

		_ahsize = isize if ahsize is None else ahsize
		_fhsize = _ahsize * 4 if fhsize is None else fhsize

		super(Decoder, self).__init__(isize, nwd, num_layer, fhsize=_fhsize, dropout=dropout, attn_drop=attn_drop, emb_w=emb_w, num_head=num_head, xseql=xseql, ahsize=_ahsize, norm_output=norm_output, bindemb=bindemb, forbidden_index=None, share_layer=share_layer, **kwargs)

		self.task_emb = nn.Embedding(ntask, isize, padding_idx=None)
		self.group_weight = nn.Parameter(torch.zeros(ntask, num_layer, 3, ngroup))
		if task_emb_w is not None:
			self.task_emb.weight = task_emb_w
		self.gw_drop = Dropout(dropout) if dropout > 0.0 else None

		self.classifier = MBLinear(isize, nwd, ntask)
		if bindemb:
			self.classifier.weight = self.wemb.weight

		if share_layer:
			_shared_layer = DecoderLayer(isize, _fhsize, dropout, attn_drop, num_head, _ahsize, ngroup=ngroup, ntask=ntask)
			self.nets = nn.ModuleList([_shared_layer for i in range(num_layer)])
		else:
			self.nets = nn.ModuleList([DecoderLayer(isize, _fhsize, dropout, attn_drop, num_head, _ahsize, ngroup=ngroup, ntask=ntask) for i in range(num_layer)])

		if norm_output:
			self.out_normer = LayerNorm(isize, ntask=ntask, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters)

		if forbidden_index is not None:
			self.fbl = [tuple(set(fblu)) for fblu in forbidden_index]

	def forward(self, inpute, inputo, taskid=None, src_pad_mask=None, **kwargs):

		nquery = inputo.size(-1)

		out = self.wemb(inputo) + self.task_emb(taskid).unsqueeze(1)
		if self.pemb is not None:
			out = self.pemb(inputo, expand=False).add(out, alpha=sqrt(out.size(-1)))

		_gw = self.group_weight.index_select(0, taskid).softmax(-1)
		if self.drop is not None:
			out = self.drop(out)
			_gw = self.gw_drop(_gw)

		_mask = self._get_subsequent_mask(nquery)

		_w = [_wu.unbind(1) for _wu in _gw.unbind(1)]
		for net, (_w_sattn, _w_cattn, _w_ffn,) in zip(self.nets, _w):
			out = net(inpute, out, sattn_w=_w_sattn, cattn_w=_w_cattn, ffn_w=_w_ffn, taskid=taskid, src_pad_mask=src_pad_mask, tgt_pad_mask=_mask)

		if self.out_normer is not None:
			out = self.out_normer(out, taskid=taskid)

		out = self.lsm(self.classifier(out, taskid))

		return out

	def load_base(self, base_decoder):

		super(Decoder, self).load_base(base_decoder)

		if hasattr(base_decoder, "task_emb"):
			self.task_emb = base_decoder.task_emb
		if hasattr(base_decoder, "group_weight"):
			self.group_weight = base_decoder.group_weight

	def decode(self, inpute, taskid=None, src_pad_mask=None, beam_size=1, max_len=512, length_penalty=0.0, fill_pad=False):

		return self.beam_decode(inpute, taskid, src_pad_mask, beam_size, max_len, length_penalty, fill_pad=fill_pad) if beam_size > 1 else self.greedy_decode(inpute, taskid, src_pad_mask, max_len, fill_pad=fill_pad)

	def greedy_decode(self, inpute, taskid=None, src_pad_mask=None, max_len=512, fill_pad=False, sample=False):

		bsize = inpute.size(0)

		out = self.get_sos_emb(inpute)
		_task_emb = self.task_emb(taskid).unsqueeze(1)

		out = out + _task_emb
		if self.pemb is not None:
			sqrt_isize = sqrt(out.size(-1))
			out = self.pemb.get_pos(0).add(out, alpha=sqrt_isize)

		_gw = self.group_weight.index_select(0, taskid).softmax(-1)
		if self.drop is not None:
			out = self.drop(out)
			_gw = self.gw_drop(_gw)

		states = {}
		_w = [_wu.unbind(1) for _wu in _gw.unbind(1)]
		for _tmp, (net, (_w_sattn, _w_cattn, _w_ffn,),) in enumerate(zip(self.nets, _w)):
			out, _state = net(inpute, (None, None,), sattn_w=_w_sattn, cattn_w=_w_cattn, ffn_w=_w_ffn, taskid=taskid, src_pad_mask=src_pad_mask, tgt_pad_mask=None, query_unit=out)
			states[_tmp] = _state

		if self.out_normer is not None:
			out = self.out_normer(out, taskid=taskid)

		out = self.classifier(out, taskid)
		wds = SampleMax(out.softmax(-1), dim=-1, keepdim=False) if sample else out.argmax(dim=-1)

		trans = [wds]

		done_trans = wds.eq(eos_id)

		for i in range(1, max_len):

			out = self.wemb(wds) + _task_emb
			if self.pemb is not None:
				out = self.pemb.get_pos(i).add(out, alpha=sqrt_isize)

			if self.drop is not None:
				out = self.drop(out)

			for _tmp, (net, (_w_sattn, _w_cattn, _w_ffn,),) in enumerate(zip(self.nets, _w)):
				out, _state = net(inpute, states[_tmp], sattn_w=_w_sattn, cattn_w=_w_cattn, ffn_w=_w_ffn, taskid=taskid, src_pad_mask=src_pad_mask, tgt_pad_mask=None, query_unit=out)
				states[_tmp] = _state

			if self.out_normer is not None:
				out = self.out_normer(out, taskid=taskid)

			out = self.classifier(out, taskid)
			wds = SampleMax(out.softmax(-1), dim=-1, keepdim=False) if sample else out.argmax(dim=-1)

			trans.append(wds.masked_fill(done_trans, pad_id) if fill_pad else wds)

			done_trans = done_trans | wds.eq(eos_id)
			if all_done(done_trans, bsize):
				break

		return torch.cat(trans, 1)

	def beam_decode(self, inpute, taskid=None, src_pad_mask=None, beam_size=8, max_len=512, length_penalty=0.0, return_all=False, clip_beam=clip_beam_with_lp, fill_pad=False):

		bsize, seql = inpute.size()[:2]

		beam_size2 = beam_size * beam_size
		bsizeb2 = bsize * beam_size2
		real_bsize = bsize * beam_size

		out = self.get_sos_emb(inpute)
		isize = out.size(-1)
		_task_emb = self.task_emb(taskid).unsqueeze(1)

		if length_penalty > 0.0:
			lpv = out.new_ones(real_bsize, 1)
			lpv_base = 6.0 ** length_penalty

		out = out + _task_emb
		if self.pemb is not None:
			sqrt_isize = sqrt(isize)
			out = self.pemb.get_pos(0).add(out, alpha=sqrt_isize)

		_gw = self.group_weight.index_select(0, taskid).softmax(-1)
		if self.drop is not None:
			out = self.drop(out)
			_gw = self.gw_drop(_gw)

		states = {}
		_w = [_wu.unbind(1) for _wu in _gw.unbind(1)]
		for _tmp, (net, (_w_sattn, _w_cattn, _w_ffn,),) in enumerate(zip(self.nets, _w)):
			out, _state = net(inpute, (None, None,), sattn_w=_w_sattn, cattn_w=_w_cattn, ffn_w=_w_ffn, taskid=taskid, src_pad_mask=src_pad_mask, tgt_pad_mask=None, query_unit=out)
			states[_tmp] = _state

		if self.out_normer is not None:
			out = self.out_normer(out, taskid=taskid)

		out = self.lsm(self.classifier(out, taskid))

		scores, wds = out.topk(beam_size, dim=-1)
		scores = scores.squeeze(1)
		sum_scores = scores
		wds = wds.view(real_bsize, 1)
		trans = wds
		_inds_add_beam2 = torch.arange(0, bsizeb2, beam_size2, dtype=wds.dtype, device=wds.device).unsqueeze(1).expand(bsize, beam_size)
		_inds_add_beam = torch.arange(0, real_bsize, beam_size, dtype=wds.dtype, device=wds.device).unsqueeze(1).expand(bsize, beam_size)

		done_trans = wds.view(bsize, beam_size).eq(eos_id)

		self.repeat_cross_attn_buffer(beam_size)

		_src_pad_mask = None if src_pad_mask is None else src_pad_mask.repeat(1, beam_size, 1).view(real_bsize, 1, seql)
		_task_emb = _task_emb.repeat(1, beam_size, 1).view(real_bsize, 1, isize)
		_taskid = None if taskid is None else taskid.unsqueeze(-1).repeat(1, beam_size).view(real_bsize)
		_w = [[_tmp.repeat(1, beam_size).view(real_bsize, -1) for _tmp in _wu] for _wu in _w]

		states = expand_bsize_for_beam(states, beam_size=beam_size)

		for step in range(1, max_len):

			out = self.wemb(wds) + _task_emb
			if self.pemb is not None:
				out = self.pemb.get_pos(step).add(out, alpha=sqrt_isize)

			if self.drop is not None:
				out = self.drop(out)

			for _tmp, (net, (_w_sattn, _w_cattn, _w_ffn,),) in enumerate(zip(self.nets, _w)):
				out, _state = net(inpute, states[_tmp], sattn_w=_w_sattn, cattn_w=_w_cattn, ffn_w=_w_ffn, taskid=taskid, src_pad_mask=_src_pad_mask, tgt_pad_mask=None, query_unit=out)
				states[_tmp] = _state

			if self.out_normer is not None:
				out = self.out_normer(out, taskid=taskid)

			out = self.lsm(self.classifier(out, _taskid)).view(bsize, beam_size, -1)

			_scores, _wds = out.topk(beam_size, dim=-1)
			_done_trans_unsqueeze = done_trans.unsqueeze(2)
			_scores = (_scores.masked_fill(_done_trans_unsqueeze.expand(bsize, beam_size, beam_size), 0.0) + sum_scores.unsqueeze(2).repeat(1, 1, beam_size).masked_fill_(select_zero_(_done_trans_unsqueeze.repeat(1, 1, beam_size), -1, 0), -inf_default))

			if length_penalty > 0.0:
				lpv.masked_fill_(~done_trans.view(real_bsize, 1), ((step + 6.0) ** length_penalty) / lpv_base)

			if clip_beam and (length_penalty > 0.0):
				scores, _inds = (_scores.view(real_bsize, beam_size) / lpv.expand(real_bsize, beam_size)).view(bsize, beam_size2).topk(beam_size, dim=-1)
				_tinds = (_inds + _inds_add_beam2).view(real_bsize)
				sum_scores = _scores.view(bsizeb2).index_select(0, _tinds).view(bsize, beam_size)
			else:
				scores, _inds = _scores.view(bsize, beam_size2).topk(beam_size, dim=-1)
				_tinds = (_inds + _inds_add_beam2).view(real_bsize)
				sum_scores = scores

			wds = _wds.view(bsizeb2).index_select(0, _tinds).view(real_bsize, 1)

			_inds = (_inds // beam_size + _inds_add_beam).view(real_bsize)

			trans = torch.cat((trans.index_select(0, _inds), wds.masked_fill(done_trans.view(real_bsize, 1), pad_id) if fill_pad else wds), 1)

			done_trans = (done_trans.view(real_bsize).index_select(0, _inds) | wds.eq(eos_id).squeeze(1)).view(bsize, beam_size)

			_done = False
			if length_penalty > 0.0:
				lpv = lpv.index_select(0, _inds)
			elif (not return_all) and all_done(done_trans.select(1, 0), bsize):
				_done = True

			if _done or all_done(done_trans, real_bsize):
				break

			states = index_tensors(states, indices=_inds, dim=0)

		if (not clip_beam) and (length_penalty > 0.0):
			scores = scores / lpv.view(bsize, beam_size)
			scores, _inds = scores.topk(beam_size, dim=-1)
			_inds = (_inds + _inds_add_beam).view(real_bsize)
			trans = trans.view(real_bsize, -1).index_select(0, _inds)

		if return_all:

			return trans.view(bsize, beam_size, -1), scores
		else:

			return trans.view(bsize, beam_size, -1).select(1, 0)

	def fix_load(self):

		if self.fbl is not None:
			with torch_no_grad():
				for ind, fblu in enumerate(self.fbl):
					self.classifier.bias[ind].index_fill_(0, torch.as_tensor(fblu, dtype=torch.long, device=self.classifier.bias.device), -inf_default)

	def fix_init(self):

		super(Decoder, self).fix_init()

		with torch_no_grad():
			self.group_weight.zero_()

	def update_vocab(self, indices):

		_nwd = len(indices)
		_wemb = nn.Embedding(_nwd, self.wemb.weight.size(-1), padding_idx=self.wemb.padding_idx)
		_classifier = MBLinear(self.classifier.weight.size(-1), _nwd, self.classifier.bias.size(0))
		with torch_no_grad():
			_wemb.weight.copy_(self.wemb.weight.index_select(0, indices))
			if self.classifier.weight.is_set_to(self.wemb.weight):
				_classifier.weight = _wemb.weight
			else:
				_classifier.weight.copy_(self.classifier.weight.index_select(0, indices))
			_classifier.bias.copy_(self.classifier.bias.index_select(1, indices))
		self.wemb, self.classifier = _wemb, _classifier
