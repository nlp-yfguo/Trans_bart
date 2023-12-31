#include <torch/extension.h>
#include <vector>

at::Tensor lgate_nocx_forward(torch::Tensor fgate, torch::Tensor igh, int64_t dim, bool inplace=false) {

	torch::Tensor cell;
	if (inplace) {
		cell = igh;
	}
	else {
		cell = igh.clone();
	}
	auto seqlen = cell.size(dim);
	int64_t i;
	for (i = 1; i < seqlen; i++) {
		cell.select(dim, i).addcmul_(cell.select(dim, i - 1), fgate.select(dim, i));
	}

	return cell;
}

std::vector<torch::Tensor> lgate_nocx_backward(torch::Tensor grad_cell, torch::Tensor cell, torch::Tensor fgate, int64_t dim) {

	torch::Tensor grad_fgate;
	auto grad_igh = grad_cell.clone();
	auto last_index = grad_cell.size(dim) - 1;
	auto acc_grad_cell = grad_cell.select(dim, last_index);
	auto grad_prev_cell = acc_grad_cell * fgate.select(dim, last_index);
	if (last_index > 0) {
		grad_fgate = grad_cell.clone();
		grad_fgate.select(dim, last_index).mul_(cell.select(dim, last_index - 1));
		int64_t i;
		for (i = last_index - 1; i > 0; i--) {
			acc_grad_cell = grad_fgate.select(dim, i).add_(grad_prev_cell);
			grad_igh.select(dim, i).add_(grad_prev_cell);
			grad_prev_cell = acc_grad_cell * fgate.select(dim, i);
			acc_grad_cell.mul_(cell.select(dim, i - 1));
		}
		grad_igh.select(dim, 0).add_(grad_prev_cell) * fgate.select(dim, 0);
		grad_fgate.select(dim, i).zero_();
	}
	else {
		grad_fgate = fgate.new_zeros(fgate.sizes());
	}

	return {grad_fgate, grad_igh};
}

torch::Tensor lgate_nocx_backward_no_fgate(torch::Tensor grad_cell, torch::Tensor fgate, int64_t dim) {

	auto grad_igh = grad_cell.clone();
	auto last_index = grad_cell.size(dim) - 1;
	auto grad_prev_cell = grad_cell.select(dim, last_index) * fgate.select(dim, last_index);
	int64_t i;
	for (i = last_index - 1; i >= 0; i--) {
		grad_prev_cell = grad_igh.select(dim, i).add_(grad_prev_cell) * fgate.select(dim, i);
	}

	return grad_igh;
}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
	m.def("forward", &lgate_nocx_forward, "LGate (No cx) forward");
	m.def("backward", &lgate_nocx_backward, "LGate (No cx) backward");
	m.def("backward_no_fgate", &lgate_nocx_backward_no_fgate, "LGate (No cx) backward (no fgate)");
}
