#encoding: utf-8

import torch
from torch import nn

from transformer.Doc.Para.Gate.BaseCAEncoder import Encoder as EncoderBase
from transformer.Encoder import EncoderLayer
from utils.doc.paragate.base4torch import clear_pad_mask

from cnfg.ihyp import *

class Encoder(EncoderBase):

	def __init__(self, isize, nwd, num_layer, fhsize=None, dropout=0.0, attn_drop=0.0, num_head=8, xseql=cache_len_default, ahsize=None, norm_output=True, nprev_context=2, num_layer_cross=1, **kwargs):

		_ahsize = isize if ahsize is None else ahsize
		_fhsize = _ahsize * 4 if fhsize is None else fhsize

		super(Encoder, self).__init__(isize, nwd, num_layer, fhsize=_fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, xseql=xseql, ahsize=_ahsize, norm_output=norm_output, nprev_context=nprev_context, num_layer_cross=num_layer_cross, **kwargs)

		self.context_genc = nn.ModuleList([EncoderLayer(isize, _fhsize, dropout, attn_drop, num_head, _ahsize) for i in range(num_layer_cross)])

	def forward(self, inputs, mask=None, **kwargs):

		# forward the whole document
		bsize, nsent, seql = inputs.size()
		sbsize = bsize * nsent
		ence_out = self.enc(inputs.view(sbsize, seql), mask.view(sbsize, 1, seql))
		isize = ence_out.size(-1)
		ence = ence_out.view(bsize, nsent, seql, isize)

		# prepare for source to context attention
		_nsent_out = nsent - 1
		ence_ctx, mask_ctx = clear_pad_mask(ence.narrow(1, 0, _nsent_out), mask.narrow(2, 0, _nsent_out), dim=2, mask_dim=-1)
		mask_ctx = mask_ctx.contiguous()
		sbsize = bsize * _nsent_out
		seql = ence_ctx.size(2)
		_ence_ctx, _mask_ctx = ence_ctx.contiguous().view(sbsize, seql, isize), mask_ctx.view(sbsize, 1, seql)
		for net in self.context_genc:
			_ence_ctx = net(_ence_ctx, _mask_ctx)
		ence_ctx = _ence_ctx.view(bsize, _nsent_out, seql, isize)

		contexts = []
		context_masks = []
		_nsent_context_base = nsent - self.nprev_context
		for i, _sent_pemb in enumerate(self.sent_pemb.unbind(0)):
			_num_context = _nsent_context_base + i
			_context, _mask = clear_pad_mask(ence_ctx.narrow(1, 0, _num_context), mask_ctx.narrow(2, 0, _num_context), dim=2, mask_dim=-1)
			_context = _context + _sent_pemb
			_num_pad = self.nprev_context - i - 1
			if _num_pad > 0:
				_seql = _context.size(2)
				_context = torch.cat((_context.new_zeros((bsize, _num_pad, _seql, isize),), _context,), dim=1)
				_mask = torch.cat((_mask.new_ones((bsize, 1, _num_pad, _seql,),), _mask,), dim=2)
			contexts.append(_context)
			context_masks.append(_mask)
		context = torch.cat(contexts, dim=2)
		context_mask = torch.cat(context_masks, dim=-1)

		# perform source to context attention
		enc_out, enc_mask = clear_pad_mask(ence.narrow(1, 1, _nsent_out), mask.narrow(2, 1, _nsent_out), dim=2, mask_dim=-1)
		_seql = enc_out.size(2)
		sbsize = bsize * _nsent_out
		enc_out, enc_mask, context, context_mask = enc_out.contiguous().view(sbsize, _seql, isize), enc_mask.contiguous().view(sbsize, 1, _seql), context.view(sbsize, -1, isize), context_mask.view(sbsize, 1, -1)
		for net in self.genc:
			enc_out = net(enc_out, context, enc_mask, context_mask)

		# original encoder output, context-aware encoding, context output, encoder mask, context mask
		return ence_out, enc_out if self.out_normer_ctx is None else self.out_normer_ctx(enc_out), context, enc_mask, context_mask

	def load_base(self, base_encoder):

		self.enc = base_encoder

	def get_loaded_para_models(self):

		return [self.enc]
