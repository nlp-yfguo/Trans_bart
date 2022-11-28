#encoding: utf-8

from modules.advdrop import AdvDropout
from transformer.Encoder import Encoder as EncoderBase
from utils.advdrop import patch_drop_attn, patch_drop_ffn

from cnfg.ihyp import *

class Encoder(EncoderBase):

	def __init__(self, isize, nwd, num_layer, fhsize=None, dropout=0.0, attn_drop=0.0, num_head=8, xseql=cache_len_default, ahsize=None, norm_output=True, share_layer=False, disable_pemb=disable_std_pemb_encoder, **kwargs):

		super(Encoder, self).__init__(isize, nwd, num_layer, fhsize=fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, xseql=xseql, ahsize=ahsize, norm_output=norm_output, share_layer=share_layer, disable_pemb=disable_pemb, **kwargs)

		if dropout > 0.0:
			self.drop = AdvDropout(dropout, isize, dim=-1)

			for net in self.nets:
				net.drop = AdvDropout(dropout, isize, dim=-1)
				net.ff = patch_drop_ffn(net.ff)
				patch_drop_attn(net)
