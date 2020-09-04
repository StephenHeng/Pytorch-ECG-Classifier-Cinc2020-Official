# ECG classifier code for Python for the PhysioNet/CinC Challenge 2020
This is a PyTorch implementation of "Multi-label Classification of Electrocardiogram with Modified Residual Networks" paper.

## Contents

This code uses two main scripts to train the model and classify the data:

* `train_model.py` Train your model. Add your model code to the `train_12ECG_model` function. It also performs all file input and output. **Do not** edit this script or we will be unable to evaluate your submission.
* `driver.py` is the classifier which calls the output from your `train_model` script. It also performs all file input and output. **Do not** edit this script or we will be unable to evaluate your submission.

Check the code in these files for the input and output formats for the `train_model` and `driver` scripts.

To create and save your model, you should edit `train_12ECG_classifier.py` script. Note that you should not change the input arguments of the `train_12ECG_classifier` function or add output arguments. The needed models and parameters should be saved in a separated file. In the sample code, an additional script, `get_12ECG_features.py`, is used to extract hand-crafted features. 

To run your classifier, you should edit the `run_12ECG_classifier.py` script, which takes a single recording as input and outputs the predicted classes and probabilities. Please, keep the formats of both outputs as they are shown in the example. You should not change the inputs and outputs of the `run_12ECG_classifier` function.

## Use

You can run this classifier code by installing the requirements and running

    python train_model.py training_data model   
    python driver.py model test_data test_outputs

where `training_data` is a directory of training data files, `model` is a directory of files for the model, `test_data` is the directory of test data files, and `test_outputs` is a directory of classifier outputs.  The [PhysioNet/CinC 2020 webpage](https://physionetchallenges.github.io/2020/) provides a training database with data files and a description of the contents and structure of these files.

## Submission

The `driver.py`, `get_12ECG_score.py`, and `get_12ECG_features.py` scripts must be in the root path of your repository. If they are inside a folder, then the submission will be unsuccessful.

## Details

See the [PhysioNet/CinC 2020 webpage](https://physionetchallenges.github.io/2020/) for more details, including dataset and instructions for the other files in this repository.

## Transfer learning using pre-trained

The [dataset](https://tianchi.aliyun.com/competition/entrance/231754/information) for pre-trained included 40,000 medical ecg samples provided by the Engineering Research Center of the Ministry of Education for mobile Health Management System of Hangzhou Normal University, China. Each sample has 8 leads, namely I, II, V1, V2, V3, V4, V5, and V6. You can also calculate the data of the remaining 4 leads by using the following formula:

III=II-I
aVR=-(I+II)/2
aVL=I-II/2
aVF=II-I/2

Each sample was sampled at a frequency of 500 HZ, a length of 10 seconds, and a unit voltage of 4.88 microvolts.

code for pre-traind is comming soon.

## Docker command
1、docker build -t image:v1.0 .

2、nvidia-docker run -it -v /home/yangshan/cinc2020_pytorch/data:/physionet/data -v /home/yangshan/cinc2020_pytorch/output:/physionet/output  -v /home/yangshan/cinc2020_pytorch/test_data:/physionet/test_data -v /home/yangshan/cinc2020_pytorch/model:/physionet/model  image:v1.0 bash

3、python train_model.py data/ model/

4、python driver.py model/ data/ output/

5、python evaluate_12ECG_score.py data/ output/

