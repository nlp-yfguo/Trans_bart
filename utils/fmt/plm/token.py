#encoding: utf-8

import sys

from utils.fmt.base import iter_to_str, loop_file_so, sys_open

tokenize_line = lambda lin, processor: " ".join(processor.convert_ids_to_tokens(processor(lin, return_token_type_ids=False, return_attention_mask=False).input_ids))
map_line = lambda lin, processor: " ".join(iter_to_str(processor(*lin.split("\t"), return_token_type_ids=False, return_attention_mask=False).input_ids))
detokenize_line = lambda lin, processor: processor(lin, skip_special_tokens=False, clean_up_tokenization_spaces=False)

def map_line_with_token_type(lin, processor):

	_ = processor(*lin.decode("utf-8").split("\t"), return_token_type_ids=True, return_attention_mask=False)

	return " ".join(iter_to_str(_.input_ids)), " ".join(iter_to_str(_.token_type_ids))

def tokenize_file(fsrc, vcb, frs, Tokenizer=None):

	return loop_file_so(fsrc, frs, process_func=tokenize_line, processor=Tokenizer(vocab_file=vcb))

def map_file(fsrc, vcb, frs, Tokenizer=None):

	return loop_file_so(fsrc, frs, process_func=map_line, processor=Tokenizer(vocab_file=vcb))

def map_file_with_token_type(fsrc, vcb, frsi, frst, Tokenizer=None):

	tokenizer = Tokenizer(tokenizer_file=vcb)
	ens = "\n".encode("utf-8")
	with sys_open(fsrc, "rb") as frd, sys_open(frsi, "wb") as fwrti, sys_open(frst, "wb") as fwrtt:
		for line in frd:
			tmp = line.strip()
			if tmp:
				_input_ids, _token_type_ids = map_line_with_token_type(tmp.decode("utf-8"), tokenizer)
				fwrti.write(_input_ids.encode("utf-8"))
				fwrti.write(ens)
				fwrtt.write(_token_type_ids.encode("utf-8"))
				fwrtt.write(ens)
			else:
				fwrti.write(ens)
				fwrtt.write(ens)

def map_back_file(fsrc, vcb, frs, Tokenizer=None):

	return loop_file_so(fsrc, frs, process_func=detokenize_line, processor=Tokenizer(vocab_file=vcb).decode)
