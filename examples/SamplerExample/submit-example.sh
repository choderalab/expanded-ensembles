#!/bin/bash
#BSUB -J "cdk"
#BSUB -n 1
#BSUB -R rusage[mem=16]
#BSUB -R span[hosts=1]
#BSUB -q gpuqueue
#BSUB -gpu num=1:j_exclusive=yes:mode=shared
#BSUB -W  12:00
#BSUB -We 11:30
#BSUB -m "ls-gpu lt-gpu lp-gpu lg-gpu"
#BSUB -o %J.stdout
##BSUB -cwd "/scratch/%U/%J"
#BSUB -eo %J.stderr
#BSUB -L /bin/bash

# quit on first error
set -e


cd $LS_SUBCWD

# Launch my program.
module load cuda/9.2
python ../../scripts/setup_relative_calculation.py cdk2_sams.yaml 
