#!/bin/bash
set -e
cd /project/code
python build_multitool_dataset.py
python split_dataset.py --input ../data/multitool_dataset_v3.jsonl --train ../data/train.jsonl --eval ../data/eval.jsonl
python train_3b.py --train ../data/train.jsonl --eval ../data/eval.jsonl --output ../models/lora
python merge_3b.py --lora ../models/lora --output ../models/merged
python convert_to_gguf.py --merged ../models/merged --output ../models/tenazas-router.gguf
python eval_runner.py --eval ../data/eval.jsonl --model tenazas-multitool --output ../results/eval.json
echo "Pipeline complete"
