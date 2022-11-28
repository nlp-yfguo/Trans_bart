#encoding: utf-8

from transformer.NMT import NMT as NMTBase
from transformer.RFN.Decoder import Decoder
from transformer.RFN.Encoder import Encoder
from utils.fmt.parser import parse_double_value_tuple
from utils.relpos.base import share_rel_pos_cache
from utils.rfn import share_LSTMCell

from cnfg.ihyp import *

class NMT(NMTBase):

	def __init__(self, isize, snwd, tnwd, num_layer, fhsize=None, dropout=0.0, attn_drop=0.0, global_emb=False, num_head=8, xseql=cache_len_default, ahsize=None, norm_output=True, bindDecoderEmb=True, forbidden_index=None, **kwargs):

		enc_layer, dec_layer = parse_double_value_tuple(num_layer)

		super(NMT, self).__init__(isize, snwd, tnwd, (enc_layer, dec_layer,), fhsize=fhsize, dropout=dropout, attn_drop=attn_drop, global_emb=global_emb, num_head=num_head, xseql=xseql, ahsize=ahsize, norm_output=norm_output, bindDecoderEmb=bindDecoderEmb, forbidden_index=forbidden_index)

		self.enc = Encoder(isize, snwd, enc_layer, fhsize=fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, xseql=xseql, ahsize=ahsize, norm_output=norm_output)

		emb_w = self.enc.wemb.weight if global_emb else None

		self.dec = Decoder(isize, tnwd, dec_layer, fhsize=fhsize, dropout=dropout, attn_drop=attn_drop, emb_w=emb_w, num_head=num_head, xseql=xseql, ahsize=ahsize, norm_output=norm_output, bindemb=bindDecoderEmb, forbidden_index=forbidden_index)

		if rel_pos_enabled:
			share_rel_pos_cache(self)
		share_LSTMCell(self, share_all=False)
