#encoding: utf-8

import torch
from torch import nn

from modules.base import Dropout, Linear
from modules.spreader.Spreader import SpreaderFunc
from modules.spreader.SpreaderNocx import SpreaderNocxFunc
from utils.init.spreader import build_spread_vector
from utils.torch.comp import flip_mask#, torch_no_grad

from cnfg.ihyp import *

class Spreader(nn.Module):

	def __init__(self, isize, hsize=None, start=2, end=8, factor=0.5, dropout=0.0, norm_residual=norm_residual_default, enable_bias=enable_prev_ln_bias_default, enable_proj_bias=enable_proj_bias_default, **kwargs):

		super(Spreader, self).__init__()

		_hsize = isize if hsize is None else hsize

		self.trans = Linear(isize, _hsize, bias=enable_proj_bias)
		self.outer = Linear(_hsize, isize, bias=enable_proj_bias)
		self.gate = nn.Sequential(Linear(isize + isize, isize, bias=enable_bias), nn.LayerNorm(isize, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters), nn.Sigmoid())
		self.normer = nn.LayerNorm(isize, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters)
		self.normer_csum = nn.LayerNorm(_hsize, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters)
		self.drop = Dropout(dropout, inplace=True) if dropout > 0.0 else None

		self.register_buffer("decay", build_spread_vector(start, end, _hsize, f=factor))
		self.register_buffer("decay_beta", 1.0 - self.decay)

		self.norm_residual = norm_residual

		#self.init_cx = nn.Parameter(torch.zeros(_hsize))

	# x: (bsize, seql, isize)
	# states: (bsize, hsize)
	# head_mask: (bsize, seql, 1)

	def forward(self, x, states=None, **kwargs):#, head_mask=None

		_x = self.normer(x)
		out = self.trans(_x).mul_(self.decay_beta)
		bsize, seql, hsize = out.size()
		#_self_decay = 1.0 - self.decay

		#cx = self.init_cx if states is None else states
		cx_out = SpreaderNocxFunc(self.decay, out, 1, False) if (states is None) or (states == "init") else SpreaderFunc(self.decay, out, states, 1, False)# if head_mask is None else LGateFunc(self.decay.view(1, 1, hsize).repeat(bsize, seql, 1).masked_fill_(head_mask, 1.0), (out * _self_decay).masked_fill_(head_mask, 0.0), cx * _self_decay, dim=1, inplace=False)

		out = self.outer(self.normer_csum(cx_out))
		_res_add = _x if self.norm_residual else x
		gate = self.gate(torch.cat((_res_add, out,), dim=-1))

		_res_add = (1.0 - gate).mul(_res_add)
		out = _res_add.addcmul_(gate, out) if self.drop is None else _res_add.add_(self.drop(out * gate))

		if states is None:
			return out
		else:
			return out, cx_out.select(1, -1)

	"""def fix_init(self):

		with torch_no_grad():
			self.init_cx.zero_()"""

class BiSpreader(Spreader):

	# x: (bsize, seql, isize)
	# mask: (bsize, seql, 1), generated by input.eq(0).view(bsize, seql, 1)
	# pad_reversed_mask: (bsize, seql, 2, 1), torch.stack((mask.new_zeros(bsize, seql, 1), mask.flip(1),), dim=2)

	def forward(self, x, mask=None, pad_reversed_mask=None, **kwargs):

		bsize, seql = x.size()[:2]
		_x = self.normer(x)
		out = self.trans(_x).mul_(self.decay_beta)
		out = torch.stack((out, out.flip(1),), dim=2)

		_r_mask = pad_reversed_mask if mask is None else torch.stack((mask.new_zeros(bsize, seql, 1), flip_mask(mask, 1),), dim=2)
		if _r_mask is not None:
			out = out.masked_fill(_r_mask, 0.0)

		#_self_decay = 1.0 - self.decay

		cx_out = SpreaderNocxFunc(self.decay, out, 1, True)

		_out_fwd, _out_rvs = cx_out.unbind(2)

		out = self.outer(self.normer_csum(_out_rvs.flip(1).add_(_out_fwd) - out))
		_res_add = _x if self.norm_residual else x
		gate = self.gate(torch.cat((_res_add, out,), dim=-1))

		_res_add = (1.0 - gate).mul(_res_add)

		return _res_add.addcmul_(gate, out) if self.drop is None else _res_add.add_(self.drop(out * gate))
