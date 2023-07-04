#!/bin/bash

set -e -o pipefail -x

export cachedir=/home/yfguo/CCMT_model/bart_wright/transformer-edge/cache/16k_Cache/
export dataid=wei2zh_bart

export srcd=$cachedir/$dataid
export srctf=src.train.bpe
export tgttf=tgt.train.tok.clean
export srcdf=src.dev.bpe
export tgtdf=tgt.dev.ch

export vsize=65536

export tgtd=$cachedir/$dataid/rs3_reverse


export srctf_id=src.train.ids
export tgttf_id=tgt.train.ids
export srctf_srt=src.train.srt
export tgttf_srt=tgt.train.srt
export rsf_train=train.h5
export srcdf_id=src.dev.ids
export tgtdf_id=tgt.dev.ids
export srcdf_srt=src.dev.srt
export tgtdf_srt=tgt.dev.srt
export rsf_dev=dev.h5

mkdir -p $tgtd
#少数民族字典使用python /tools/vocab/token/single.py
export src_vcb=/home/yfguo/zh_data_for_vcb/wei2zh/true_reverse_wei.vcb
export tgt_vcb=/home/yfguo/CCMT_model/bart_wright/bart-base-chinese/vocab.txt

#首先需要生成少数民族字典用于转id使用，这个也是以后做解码时使用的字典
#python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/vocab/token/single.py $srcd/$srctf $src_vcb $vsize

export ngpu=1
export maxtokens=512
export sort_decode=true



# tgt为bart中的中文字典，继续使用即可，不需要额外动
python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/plm/map/bart.py $srcd/$tgttf $tgt_vcb $tgtd/$tgttf_id &
python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/plm/map/bart.py $srcd/$tgtdf $tgt_vcb $tgtd/$tgtdf_id &
wait

#需要使用map.py转换少数民族语言id
python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/vocab/new_map.py $srcd/$srctf $src_vcb $tgtd/$srctf_id &
python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/vocab/new_map.py $srcd/$srcdf $src_vcb $tgtd/$srcdf_id &
wait


if $sort_decode; then
  export srctf_s=$tgtd/$srctf_srt
	export tgttf_s=$tgtd/$tgttf_srt
  export srcdf_s=$tgtd/$srcdf_srt
	export tgtdf_s=$tgtd/$tgtdf_srt
  python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/sort.py $tgtd/$srcdf_id $tgtd/$tgtdf_id $srcdf_s $tgtdf_s $maxtokens &
  python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/sort.py $tgtd/$srctf_id $tgtd/$tgttf_id $srctf_s $tgttf_s $maxtokens &
wait
else
	export srctf_s=$tgtd/$srctf_id
	export tgttf_s=$tgtd/$tgttf_id
	export srcdf_s=$tgtd/$srcdf_id
	export tgtdf_s=$tgtd/$tgtdf_id
fi


python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/plm/mkbart_data.py $srcdf_s $tgtdf_s $tgtd/$rsf_dev $ngpu &
python /home/yfguo/CCMT_model/bart_wright/transformer-edge/tools/plm/mkbart_data.py $srctf_s $tgttf_s $tgtd/$rsf_train $ngpu &
wait

