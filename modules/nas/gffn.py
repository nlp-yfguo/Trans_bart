#encoding: utf-8

import torch
from torch import nn

from modules.act import Custom_Act
from modules.base import Dropout, Linear, PositionwiseFF as PositionwiseFFBase
from utils.fmt.parser import parse_none

from cnfg.ihyp import *

class Cell(nn.Module):

	def __init__(self, num_node, isize, dropout=0.0, act_dropout=None, custom_act=use_adv_act_default, enable_bias=enable_prev_ln_bias_default, **kwargs):

		super(Cell, self).__init__()
		_act_dropout = parse_none(act_dropout, dropout)

		self.net0 = nn.Sequential(Linear(isize, isize * (num_node - 1)), Custom_Act() if custom_act else nn.ReLU(inplace=True), Dropout(_act_dropout, inplace=inplace_after_Custom_Act)) if _act_dropout > 0.0 else nn.Sequential(Linear(isize, isize * (num_node - 1)), Custom_Act() if custom_act else nn.ReLU(inplace=True))
		self.net1 = nn.Sequential(Linear(isize, isize), Custom_Act() if custom_act else nn.ReLU(inplace=True), Dropout(_act_dropout, inplace=inplace_after_Custom_Act)) if _act_dropout > 0.0 else nn.Sequential(Linear(isize, isize), Custom_Act() if custom_act else nn.ReLU(inplace=True))
		self.trans = nn.Sequential(Linear(isize * num_node, isize, bias=enable_bias), Dropout(dropout, inplace=True)) if dropout > 0.0 else Linear(isize * num_node, isize, bias=enable_bias)

	def forward(self, x, **kwargs):

		out = self.net0(x)
		outs = [out, self.net1(out.narrow(-1, 0, x.size(-1)))]

		return self.trans(torch.cat(outs, -1))

class PositionwiseFF(PositionwiseFFBase):

	def __init__(self, isize, hsize=None, dropout=0.0, act_dropout=None, **kwargs):

		_hsize = isize * 4 if hsize is None else hsize
		_act_dropout = parse_none(act_dropout, dropout)

		super(PositionwiseFF, self).__init__(isize, hsize=_hsize, dropout=dropout, act_dropout=_act_dropout, **kwargs)

		self.net = Cell(max(1, _hsize // isize), isize, dropout=dropout, act_dropout=_act_dropout)

	def load_base(self, base_module):

		self.normer, self.norm_residual = base_module.normer, base_module.norm_residual
