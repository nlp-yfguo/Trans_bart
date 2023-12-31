#!/bin/bash

set -e -o pipefail -x

export srcd=cache/gector
export srctf=src.test.txt
export modelf=""
export rsd=$srcd
export rsf=$rsd/pred.test.txt
export src_vcb=~/plm/custbert/char.vcb

export cachedir=cache
export dataid=gector

export ngpu=1

export sort_decode=true

export faext=".xz"

export tgtd=$cachedir/$dataid

export tgt_vcb=$src_vcb
export bpef=out.bpe

mkdir -p $rsd

export stif=$tgtd/$srctf.ids$faext
python tools/plm/map/custbert.py $srcd/$srctf $src_vcb $stif
if $sort_decode; then
	export srt_input_f=$tgtd/$srctf.ids.srt$faext
	python tools/sort.py $tgtd/$srctf.ids $srt_input_f 1048576
else
	export srt_input_f=$stif
fi

python tools/plm/mktest.py $srt_input_f $tgtd/test.h5 $ngpu
python predict_gector.py $tgtd/$bpef $tgt_vcb $modelf

if $sort_decode; then
	python tools/restore.py $stif $srt_input_f $tgtd/$bpef $rsf
	rm $srt_input_f $tgtd/$bpef
else
	mv $tgtd/$bpef $rsf
fi
rm $stif $tgtd/test.h5
