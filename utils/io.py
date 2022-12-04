#encoding: utf-8

from os.path import exists as fs_check
from threading import Thread

from utils.h5serial import h5load, h5save
from utils.torch.comp import torch_no_grad

from cnfg.ihyp import h5modelwargs, n_keep_best

def load_model_cpu(modf, base_model):

	with torch_no_grad():
		for para, mp in zip(base_model.parameters(), h5load(modf)):
			para.copy_(mp)

	return base_model

def load_model_cpu_old(modf, base_model):

	base_model.load_state_dict(h5load(modf))

	return base_model

class bestfkeeper:

	def __init__(self, fnames=None, k=n_keep_best, **kwargs):

		self.fnames, self.k = [] if fnames is None else fnames, k
		self.clean()

	def update(self, fname=None):

		self.fnames.append(fname)
		self.clean(last_fname=fname)

	def clean(self, last_fname=None):

		_n_files = len(self.fnames)
		_last_fname = (self.fnames[-1] if self.fnames else None) if last_fname is None else last_fname
		while _n_files > self.k:
			fname = self.fnames.pop(0)
			if (fname is not None) and (fname != _last_fname) and fs_check(fname):
				try:
					remove(fname)
				except Exception as e:
					print(e)
			_n_files -= 1

class SaveModelCleaner:

	def __init__(self):

		self.holder = {}

	def __call__(self, fname, typename, **kwargs):

		if typename in self.holder:
			self.holder[typename].update(fname)
		else:
			self.holder[typename] = bestfkeeper(fnames=[fname])

save_model_cleaner = SaveModelCleaner()

def save_model(model, fname, sub_module=False, print_func=print, mtyp=None, h5args=h5modelwargs):

	_msave = model.module if sub_module else model
	try:
		h5save([t.data for t in _msave.parameters()], fname, h5args=h5args)
		if mtyp is not None:
			save_model_cleaner(fname, mtyp)
	except Exception as e:
		if print_func is not None:
			print_func(str(e))

def async_save_model(model, fname, sub_module=False, print_func=print, mtyp=None, h5args=h5modelwargs, para_lock=None, log_success=None):

	def _worker(model, fname, sub_module=False, print_func=print, mtyp=None, para_lock=None, log_success=None):

		success = True
		_msave = model.module if sub_module else model
		try:
			if para_lock is None:
				h5save([t.data for t in _msave.parameters()], fname, h5args=h5args)
				if mtyp is not None:
					save_model_cleaner(fname, mtyp)
			else:
				with para_lock:
					h5save([t.data for t in _msave.parameters()], fname, h5args=h5args)
					if mtyp is not None:
						save_model_cleaner(fname, mtyp)
		except Exception as e:
			if print_func is not None:
				print_func(str(e))
			success = False
		if success and (print_func is not None) and (log_success is not None):
			print_func(str(log_success))

	Thread(target=_worker, args=(model, fname, sub_module, print_func, mtyp, para_lock, log_success)).start()

def save_states(state_dict, fname, print_func=print, mtyp=None):

	try:
		torch.save(state_dict, fname)
		if mtyp is not None:
			save_model_cleaner(fname, mtyp)
	except Exception as e:
		if print_func is not None:
			print_func(str(e))
