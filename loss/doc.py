#encoding: utf-8

from torch.nn.functional import kl_div

from loss.base import StdLabelSmoothingLoss
from utils.base import eq_indexes
from utils.doc.paragate.base4torch import clear_pad_mask as clear_pad_mask_doc

class ReducedDocLabelSmoothingLoss(StdLabelSmoothingLoss):

	def forward(self, input, target, mask=None, **kwargs):

		_target = clear_pad_mask_doc(target, target.eq(0), dim=-1, mask_dim=-1)[0].contiguous()

		_input = input.view(-1, input.size(-1)) if input.dim() > 2 else input
		_target = _target.view(-1, 1)

		model_prob = self.weight.repeat(_target.size(0), 1)
		model_prob.scatter_(1, _target, self.conf)

		_pad_mask = mask
		if _pad_mask is None:
			if isinstance(self.ignore_index, (list, tuple,)):
				_pad_mask = eq_indexes(_target, self.ignore_index)
			elif self.ignore_index >= 0:
				_pad_mask = _target.eq(self.ignore_index)
		else:
			_pad_mask = _pad_mask.view(-1, 1)
		if _pad_mask is not None:
			model_prob.masked_fill_(_pad_mask, 0.0)

		rs = kl_div(_input, model_prob, reduction=self.reduction)

		return rs.view(input.size()) if self.reduction == "none" and target.dim() > 1 else rs
