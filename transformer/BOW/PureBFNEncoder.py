#encoding: utf-8

# import by transformer.NMT instead of transformer.BOW.NMT
from math import sqrt
from torch import nn

from modules.bow import DBOW as BOW
from transformer.Encoder import Encoder as EncoderBase

from cnfg.ihyp import *

class Encoder(EncoderBase):

	def __init__(self, isize, nwd, num_layer, fhsize=None, dropout=0.0, attn_drop=0.0, num_head=8, xseql=cache_len_default, ahsize=None, share_layer=False, window_size=5, **kwargs):

		_ahsize = isize if ahsize is None else ahsize
		_fhsize = _ahsize * 4 if fhsize is None else fhsize

		super(Encoder, self).__init__(isize, nwd, num_layer, fhsize=_fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, xseql=xseql, ahsize=_ahsize, share_layer=share_layer, **kwargs)

		if share_layer:
			_shared_layer = BOW(isize, _fhsize, isize, num_head=window_size, dropout=dropout)
			self.nets = nn.ModuleList([_shared_layer for i in range(num_layer * 2)])
		else:
			self.nets = nn.ModuleList([BOW(isize, _fhsize, isize, num_head=window_size, dropout=dropout) for i in range(num_layer * 2)])

	def forward(self, inputs, mask=None, **kwargs):

		out = self.wemb(inputs)
		out = out * sqrt(out.size(-1))
		if self.pemb is not None:
			out = out + self.pemb(inputs, expand=False)

		if self.drop is not None:
			out = self.drop(out)

		bsize, seql = inputs.size()[:2]
		_mask = mask if mask is None else mask.view(bsize, seql, 1)

		for net in self.nets:
			out = net(out, mask=_mask)

		return out if self.out_normer is None else self.out_normer(out)
