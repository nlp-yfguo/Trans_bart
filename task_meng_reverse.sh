#!/bin/bash
set -e -o pipefail -x
source /home/yfguo/scripts/init_conda.sh
conda activate Trans_base

python true_reverse_dict_meng_train_bart.py