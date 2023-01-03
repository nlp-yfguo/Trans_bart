#encoding: utf-8

import sys

from utils.fmt.plm.custbert.token import Tokenizer, map_line
from utils.fmt.plm.token import loop_file_so

def handle(fsrc, vcb, frs, split=False):

	return loop_file_so(fsrc, frs, process_func=map_line, processor=Tokenizer(vcb, split=split))

if __name__ == "__main__":
	handle(sys.argv[1], sys.argv[2], sys.argv[3])
