#encoding: utf-8

from transformer.MIMO.Decoder import Decoder
from transformer.MIMO.Encoder import Encoder
from transformer.NMT import NMT as NMTBase
from utils.fmt.parser import parse_double_value_tuple
from utils.relpos.base import share_rel_pos_cache

from cnfg.ihyp import *
from cnfg.vocab.base import pad_id

class NMT(NMTBase):

	def __init__(self, isize, snwd, tnwd, num_layer, fhsize=None, dropout=0.0, attn_drop=0.0, global_emb=False, num_head=8, xseql=cache_len_default, ahsize=None, norm_output=True, bindDecoderEmb=True, forbidden_index=None, nmimo=4, **kwargs):

		enc_layer, dec_layer = parse_double_value_tuple(num_layer)

		super(NMT, self).__init__(isize, snwd, tnwd, (enc_layer, dec_layer,), fhsize=fhsize, dropout=dropout, attn_drop=attn_drop, global_emb=global_emb, num_head=num_head, xseql=xseql, ahsize=ahsize, norm_output=norm_output, bindDecoderEmb=bindDecoderEmb, forbidden_index=forbidden_index)

		self.enc = Encoder(isize, snwd, enc_layer, fhsize=fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, xseql=xseql, ahsize=ahsize, norm_output=norm_output, nmimo=nmimo)

		emb_w = self.enc.wemb.weight if global_emb else None

		self.dec = Decoder(isize, tnwd, dec_layer, fhsize=fhsize, dropout=dropout, attn_drop=attn_drop, emb_w=emb_w, num_head=num_head, xseql=xseql, ahsize=ahsize, norm_output=norm_output, bindemb=bindDecoderEmb, forbidden_index=forbidden_index, nmimo=nmimo)

		if rel_pos_enabled:
			share_rel_pos_cache(self)

	def forward(self, inpute, inputo, mask=None, **kwargs):

		_mask = inpute.eq(pad_id).unsqueeze(1) if mask is None else mask

		if self.training:
			ence, rind, _mask = self.enc(inpute, _mask)
		else:
			ence, rind = self.enc(inpute, _mask), None

		if rind is None:
			return self.dec(ence, inputo, src_pad_mask=_mask, ind=rind)
		else:
			return self.dec(ence, inputo, src_pad_mask=_mask, ind=rind), rind

	def decode(self, inpute, beam_size=1, max_len=None, length_penalty=0.0, ensemble_decoding=False):

		mask = inpute.eq(pad_id).unsqueeze(1)

		_max_len = (inpute.size(1) + max(64, inpute.size(1) // 4)) if max_len is None else max_len
		if ensemble_decoding:
			ence = self.enc(inpute, mask)
		else:
			ence, mask = self.enc(inpute, mask)

		return self.dec.decode(ence, mask, beam_size, _max_len, length_penalty, ensemble_decoding=ensemble_decoding, bsize=inpute.size(0))
