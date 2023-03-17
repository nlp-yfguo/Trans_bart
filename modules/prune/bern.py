#encoding: utf-8

import torch
from math import log, sqrt
from numbers import Integral
from torch import nn
from torch.autograd import Function
from torch.nn import functional as nnFunc
from torch.nn.init import _calculate_fan_in_and_fan_out

from modules.act import Custom_Act
from modules.base import CrossAttn as CrossAttnBase, Dropout, PositionwiseFF as PositionwiseFFBase, ResCrossAttn as ResCrossAttnBase, ResSelfAttn as ResSelfAttnBase, SelfAttn as SelfAttnBase
from utils.init.base import kaiming_uniform_
from utils.torch.comp import mask_tensor_type, torch_no_grad

from cnfg.ihyp import *

class BernoulliMaskFunction(Function):

	# Note that both forward and backward are @staticmethods
	@staticmethod
	def forward(ctx, inputs, maskp, inplace=False):

		_mask = maskp.bernoulli()
		mask, _nd = _mask.to(mask_tensor_type, non_blocking=True), float(_mask.numel())
		ctx.save_for_backward(inputs, mask)
		return inputs.masked_fill_(mask, 0.0) if inplace else inputs.masked_fill(mask, 0.0), _nd / (_nd - _mask.sum())

	@staticmethod
	def backward(ctx, grad_outputs, grad_scale):

		if grad_outputs is None:
			return None, None, None
		else:
			inputs, mask = ctx.saved_tensors
			_grad_input = grad_outputs.masked_fill(mask, 0.0) if ctx.needs_input_grad[0] else None
			# 0s generated by bernoulli (sigmoid) for masking indicates * 1 (1 - 0), thus gradients to sigmoid should be reversed
			_grad_maskp = -grad_outputs * inputs if ctx.needs_input_grad[1] else None
			return _grad_input, _grad_maskp, None

BernoulliMaskFunc = BernoulliMaskFunction.apply

class BernoulliParameter(nn.Module):

	def __init__(self, tensor_in, init_p_value=0.5, host_mask=True, auto_mask=False, **kwargs):

		super(BernoulliParameter, self).__init__()

		self.data = nn.Parameter(tensor_in)
		self.init_value = None if init_p_value is None or init_p_value <= 0.0 or init_p_value >= 1.0 else log(1.0 / init_p_value - 1.0)
		self.maskp = nn.Parameter(tensor_in.abs().detach()) if self.init_value is None else nn.Parameter(tensor_in.new_empty(tensor_in.size()).fill_(self.init_value))

		self.host_mask, self.auto_mask = host_mask, auto_mask
		self.masked_data = None
		self.ignore_mask = False
		self.register_buffer("fixed_mask", None, persistent=False)

	def forward(self):

		if self.auto_mask and (self.fixed_mask is not None):
			return self.data.masked_fill(self.fixed_mask, 0.0)
		elif self.ignore_mask or self.maskp is None:
			return self.data
		else:
			if self.host_mask and self.masked_data is not None:
				return self.masked_data
			else:
				output, _scale = BernoulliMaskFunc(self.data, self.maskp.sigmoid(), False)
				output = output * _scale
				if self.host_mask:
					self.masked_data = output

				return output

	def reset(self):
		self.masked_data = None

	def prune(self, thres=0.0):

		if self.maskp is not None:
			with torch_no_grad():
				self.fixed_mask = self.maskp.ge(thres)
				self.data.masked_fill_(self.fixed_mask, 0.0)
				self.maskp = None

	def fix_init(self):

		if self.maskp is not None:
			with torch_no_grad():
				self.maskp.copy_(self.abs().data) if self.init_value is None else self.maskp.fill_(self.init_value)

	def use_mask(self, value):

		self.ignore_mask = not value
		if self.maskp is not None:
			self.maskp.requires_grad_(value)

class Linear(nn.Module):

	def __init__(self, in_features, out_features, bias=True, **kwargs):
		super(Linear, self).__init__()
		self.in_features = in_features
		self.out_features = out_features
		self.weight = BernoulliParameter(torch.Tensor(out_features, in_features))
		if bias:
			self.bias = BernoulliParameter(torch.Tensor(out_features))
		else:
			self.register_parameter("bias", None)
		self.reset_parameters()

	def reset_parameters(self):
		with torch_no_grad():
			kaiming_uniform_(self.weight.data, gain=sqrt(1.0/3.0))
			if self.bias is not None:
				fan_in, _ = _calculate_fan_in_and_fan_out(self.weight.data)
				bound = 1.0 / sqrt(fan_in)
				self.bias.data.uniform_(-bound, bound)

	def forward(self, input, **kwargs):

		return nnFunc.linear(input, self.weight(), None if self.bias is None else self.bias())

	def extra_repr(self):
		return "in_features={}, out_features={}, bias={}".format(self.in_features, self.out_features, self.bias is not None)

class LinearBn(nn.Module):

	def __init__(self, in_features, out_features, bias=True, **kwargs):
		super(LinearBn, self).__init__()
		self.in_features = in_features
		self.out_features = out_features
		self.weight = BernoulliParameter(torch.Tensor(out_features, in_features))
		if bias:
			self.bias = nn.Parameter(torch.Tensor(out_features))
		else:
			self.register_parameter("bias", None)
		self.reset_parameters()

	def reset_parameters(self):
		with torch_no_grad():
			kaiming_uniform_(self.weight.data, gain=sqrt(1.0/3.0))
			if self.bias is not None:
				fan_in, _ = _calculate_fan_in_and_fan_out(self.weight.data)
				bound = 1.0 / sqrt(fan_in)
				self.bias.uniform_(-bound, bound)

	def forward(self, input, **kwargs):

		return nnFunc.linear(input, self.weight(), None if self.bias is None else self.bias)

	def extra_repr(self):
		return "in_features={}, out_features={}, bias={}".format(self.in_features, self.out_features, self.bias is not None)

class Embedding(nn.Module):

	def __init__(self, num_embeddings, embedding_dim, padding_idx=None, max_norm=None, norm_type=2., scale_grad_by_freq=False, sparse=False, _weight=None, prune_ratio=128.0, **kwargs):
		super(Embedding, self).__init__()
		self.num_embeddings = num_embeddings
		self.embedding_dim = embedding_dim
		if padding_idx is not None:
			if padding_idx > 0:
				assert padding_idx < self.num_embeddings, "Padding_idx must be within num_embeddings"
			elif padding_idx < 0:
				assert padding_idx >= -self.num_embeddings, "Padding_idx must be within num_embeddings"
				padding_idx = self.num_embeddings + padding_idx
		self.padding_idx = padding_idx
		self.max_norm = max_norm
		self.norm_type = norm_type
		self.scale_grad_by_freq = scale_grad_by_freq
		if _weight is None:
			self.weight = BernoulliParameter(torch.Tensor(num_embeddings, embedding_dim))
			self.reset_parameters()
		else:
			assert list(_weight.shape) == [num_embeddings, embedding_dim], \
				"Shape of weight does not match num_embeddings and embedding_dim"
			self.weight = BernoulliParameter(_weight)
		self.sparse = sparse

	def reset_parameters(self):
		with torch_no_grad():
			self.weight.data.normal_()
			if self.padding_idx is not None:
				self.weight.data[self.padding_idx].fill_(0)

	def forward(self, input, **kwargs):

		return nnFunc.embedding(input, self.weight(), self.padding_idx, self.max_norm, self.norm_type, self.scale_grad_by_freq, self.sparse)

	def extra_repr(self):
		s = "{num_embeddings}, {embedding_dim}"
		if self.padding_idx is not None:
			s += ", padding_idx={padding_idx}"
		if self.max_norm is not None:
			s += ", max_norm={max_norm}"
		if self.norm_type != 2:
			s += ", norm_type={norm_type}"
		if self.scale_grad_by_freq is not False:
			s += ", scale_grad_by_freq={scale_grad_by_freq}"
		if self.sparse is not False:
			s += ", sparse=True"
		return s.format(**self.__dict__)

	def from_pretrained(cls, embeddings, freeze=True, padding_idx=None, max_norm=None, norm_type=2., scale_grad_by_freq=False, sparse=False):

		assert embeddings.dim() == 2, \
			"Embeddings parameter is expected to be 2-dimensional"
		rows, cols = embeddings.shape
		embedding = cls(
			num_embeddings=rows,
			embedding_dim=cols,
			_weight=embeddings,
			padding_idx=padding_idx,
			max_norm=max_norm,
			norm_type=norm_type,
			scale_grad_by_freq=scale_grad_by_freq,
			sparse=sparse)
		embedding.weight.requires_grad = not freeze
		return embedding

class LayerNorm(nn.Module):

	def __init__(self, normalized_shape, eps=1e-5, elementwise_affine=True, **kwargs):
		super(LayerNorm, self).__init__()
		if isinstance(normalized_shape, Integral):
			normalized_shape = (normalized_shape,)
		self.normalized_shape = tuple(normalized_shape)
		self.eps = eps
		self.elementwise_affine = elementwise_affine
		if self.elementwise_affine:
			self.weight = BernoulliParameter(torch.Tensor(*normalized_shape))
			self.bias = BernoulliParameter(torch.Tensor(*normalized_shape))
		else:
			self.register_parameter("weight", None)
			self.register_parameter("bias", None)
		self.reset_parameters()

	def reset_parameters(self):
		if self.elementwise_affine:
			with torch_no_grad():
				self.weight.data.fill_(1.0)
				self.bias.data.zero_()

	def forward(self, input, **kwargs):

		return nnFunc.layer_norm(input, self.normalized_shape, self.weight(), self.bias(), self.eps)

	def extra_repr(self):
		return "{normalized_shape}, eps={eps}, " \
			"elementwise_affine={elementwise_affine}".format(**self.__dict__)

class PositionwiseFF(PositionwiseFFBase):

	def __init__(self, isize, hsize=None, dropout=0.0, norm_residual=norm_residual_default, custom_act=use_adv_act_default, enable_bias=enable_prev_ln_bias_default, **kwargs):

		_hsize = isize * 4 if hsize is None else hsize

		super(PositionwiseFF, self).__init__(isize, hsize=_hsize, dropout=dropout, norm_residual=norm_residual, custom_act=custom_act, enable_bias=enable_bias)

		self.net = nn.Sequential(LinearBn(isize, _hsize), Custom_Act() if custom_act else nn.ReLU(inplace=True), Dropout(dropout, inplace=inplace_after_Custom_Act), LinearBn(_hsize, isize, bias=enable_bias), Dropout(dropout, inplace=True)) if dropout > 0.0 else nn.Sequential(LinearBn(isize, _hsize), Custom_Act() if custom_act else nn.ReLU(inplace=True), LinearBn(_hsize, isize, bias=enable_bias))

		##self.normer = LayerNorm(isize, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters)

class SelfAttn(SelfAttnBase):

	def __init__(self, isize, hsize, osize, **kwargs):

		super(SelfAttn, self).__init__(isize, hsize, osize, enable_bias=enable_prev_ln_bias_default, enable_proj_bias=enable_proj_bias_default, **kwargs)

		self.adaptor = LinearBn(isize, self.hsize * 3, bias=enable_proj_bias)

		self.outer = LinearBn(self.hsize, osize, bias=enable_bias)

class CrossAttn(CrossAttnBase):

	def __init__(self, isize, hsize, osize, enable_bias=enable_prev_ln_bias_default, enable_proj_bias=enable_proj_bias_default, **kwargs):

		super(CrossAttn, self).__init__(isize, hsize, osize, enable_bias=enable_bias, enable_proj_bias=enable_proj_bias, **kwargs)

		self.query_adaptor = LinearBn(isize, self.hsize, bias=enable_proj_bias)

		self.kv_adaptor = LinearBn(isize if k_isize is None else k_isize, self.hsize * 2, bias=enable_proj_bias)

		self.outer = LinearBn(self.hsize, osize, bias=enable_bias)

class ResSelfAttn(ResSelfAttnBase):

	def __init__(self, isize, hsize, num_head=8, dropout=0.0, norm_residual=norm_residual_default, **kwargs):

		super(ResSelfAttn, self).__init__(isize, hsize, num_head=num_head, dropout=dropout, norm_residual=norm_residual, **kwargs)

		self.net = SelfAttn(isize, hsize, isize, num_head=num_head, dropout=dropout, **kwargs)
		#self.normer = LayerNorm(isize, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters)

class ResCrossAttn(ResCrossAttnBase):

	def __init__(self, isize, hsize, num_head=8, dropout=0.0, norm_residual=norm_residual_default, **kwargs):

		super(ResCrossAttn, self).__init__(isize, hsize, num_head=num_head, dropout=dropout, norm_residual=norm_residual, **kwargs)

		self.net = CrossAttn(isize, hsize, isize, num_head=num_head, dropout=dropout, **kwargs)
		#self.normer = LayerNorm(isize, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters)
