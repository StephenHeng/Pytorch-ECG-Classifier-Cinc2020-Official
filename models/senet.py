"""
SEResNet implementation from Cadene's pretrained models
https://github.com/Cadene/pretrained-models.pytorch/blob/master/pretrainedmodels/models/senet.py
Additional credit to https://github.com/creafz
Original model: https://github.com/hujie-frank/SENet
ResNet code gently borrowed from
https://github.com/pytorch/vision/blob/master/torchvision/models/resnet.py
"""

# ref:https://github.com/rwightman/pytorch-image-models/blob/master/timm/models/senet.py

from collections import OrderedDict
import math
import torch
import torch.nn as nn
import torch.nn.functional as F

from .utils import SelectAdaptivePool1d

__all__ = ['SENet']

class Attention(nn.Module):
    def __init__(self, feature_dim, step_dim, bias=True, **kwargs):
        super(Attention, self).__init__(**kwargs)
        
        self.supports_masking = True

        self.bias = bias
        self.feature_dim = feature_dim
        self.step_dim = step_dim
        self.features_dim = 0
        
        weight = torch.zeros(feature_dim, 1)
        nn.init.xavier_uniform_(weight)
        self.weight = nn.Parameter(weight)
        
        if bias:
            self.b = nn.Parameter(torch.zeros(step_dim))
        
    def forward(self, x, mask=None):
        feature_dim = self.feature_dim
        step_dim = self.step_dim

        eij = torch.mm(
            x.contiguous().view(-1, feature_dim), 
            self.weight
        ).view(-1, step_dim)
        
        if self.bias:
            eij = eij + self.b
            
        eij = torch.tanh(eij)
        a = torch.exp(eij)
        
        if mask is not None:
            a = a * mask

        a = a / torch.sum(a, 1, keepdim=True) + 1e-10

        weighted_input = x * torch.unsqueeze(a, -1)
        return torch.sum(weighted_input, 1)
    
def _weight_init(m):
    if isinstance(m, nn.Conv2d):
        nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
    elif isinstance(m, nn.BatchNorm2d):
        nn.init.constant_(m.weight, 1.)
        nn.init.constant_(m.bias, 0.)

# ref:https://github.com/c0nn3r/pytorch_highway_networks/blob/master/main.py
class HighwayMLP(nn.Module):

    def __init__(self,
                 input_size,
                 gate_bias=-2,
                 activation_function=nn.functional.relu,
                 gate_activation=nn.functional.softmax):

        super(HighwayMLP, self).__init__()

        self.activation_function = activation_function
        self.gate_activation = gate_activation

        self.normal_layer = nn.Linear(input_size, input_size)

        self.gate_layer = nn.Linear(input_size, input_size)
        self.gate_layer.bias.data.fill_(gate_bias)

    def forward(self, x):

        normal_layer_result = self.activation_function(self.normal_layer(x))
        gate_layer_result = self.gate_activation(self.gate_layer(x))

        multiplyed_gate_and_normal = torch.mul(normal_layer_result, gate_layer_result)
        multiplyed_gate_and_input = torch.mul((1 - gate_layer_result), x)

        return torch.add(multiplyed_gate_and_normal,
                         multiplyed_gate_and_input)
    
#ref: https://www.cnblogs.com/ansang/p/9371764.html
#ref:https://github.com/rwightman/pytorch-image-models/blob/master/timm/models/layers/cbam.py
class CBAM_Module(nn.Module):

    def __init__(self, channels, reduction):
        super(CBAM_Module, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool1d(1)
        self.max_pool = nn.AdaptiveMaxPool1d(1)
        self.fc1 = nn.Conv1d(channels, channels // reduction, kernel_size=1,
                             padding=0)
        self.relu = nn.ReLU(inplace=True)
        self.fc2 = nn.Conv1d(channels // reduction, channels, kernel_size=1,
                             padding=0)
        self.sigmoid_channel = nn.Sigmoid()
        self.conv_after_concat = nn.Conv1d(2, 1, kernel_size=3, stride=1, padding=1)
        self.sigmoid_spatial = nn.Sigmoid()

    def forward(self, x):
        # Channel attention module:（Mc(f) = σ(MLP(AvgPool(f)) + MLP(MaxPool(f)))）
        module_input = x
        avg = self.avg_pool(x)
        mx = self.max_pool(x)
        avg = self.fc1(avg)
        mx = self.fc1(mx)
        avg = self.relu(avg)
        mx = self.relu(mx)
        avg = self.fc2(avg)
        mx = self.fc2(mx)
        x = avg + mx
        x = self.sigmoid_channel(x)
        # Spatial attention module:Ms (f) = σ( f7×7( AvgPool(f) ; MaxPool(F)] )))
        x = module_input * x
        module_input = x
        avg = torch.mean(x, 1, keepdim=True)
        mx, _ = torch.max(x, 1, keepdim=True)
        x = torch.cat((avg, mx), 1)
        x = self.conv_after_concat(x)
        x = self.sigmoid_spatial(x)
        x = module_input * x
        return x


class SCSEModule(nn.Module):
    def __init__(self, channels, reduction):
        super(SCSEModule, self).__init__()
        self.cSE = nn.Sequential(nn.AdaptiveAvgPool1d(1),
                                 nn.Conv1d(channels,channels//reduction, kernel_size=1, padding=0),
                                 nn.ReLU(inplace=True),
                                 nn.Conv1d(channels//reduction,channels, kernel_size=1, padding=0),
                                 nn.Sigmoid())

        self.sSE = nn.Sequential(nn.Conv1d(channels, channels, kernel_size=1, padding=0),
                                 nn.Sigmoid())

    def forward(self, x):
        return x * self.cSE(x) + x * self.sSE(x)

class SEModule(nn.Module):

    def __init__(self, channels, reduction):
        assert channels > reduction, "Make sure your input channel bigger than reduction which equals to {}".format(reduction)
        super(SEModule, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool1d(1)
        self.fc1 = nn.Conv1d(channels, channels // reduction, kernel_size=1, padding=0)
        self.relu = nn.ReLU(inplace=True)
        self.fc2 = nn.Conv1d(channels // reduction, channels, kernel_size=1, padding=0)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        module_input = x
        x = self.avg_pool(x)
        x = self.fc1(x)
        x = self.relu(x)
        x = self.fc2(x)
        x = self.sigmoid(x)
        return module_input * x


class Bottleneck(nn.Module):
    """
    Base class for bottlenecks that implements `forward()` method.
    """

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)
        
        if self.downsample is not None:
           residual = self.downsample(x)
        
        out = self.se_module(out) + residual
        out = self.relu(out)

        return out


class SEBottleneck(Bottleneck):
    """
    Bottleneck for SENet154.
    """
    expansion = 4

    def __init__(self, inplanes, planes, groups, reduction, stride=1,
                 downsample=None):
        super(SEBottleneck, self).__init__()
        self.conv1 = nn.Conv1d(inplanes, planes * 2, kernel_size=1, bias=False)
        self.bn1 = nn.BatchNorm1d(planes * 2)
        self.conv2 = nn.Conv1d(planes * 2, planes * 4, kernel_size=3, stride=stride,
            padding=1, groups=groups, bias=False)
        self.bn2 = nn.BatchNorm1d(planes * 4)
        self.conv3 = nn.Conv1d(planes * 4, planes * 4, kernel_size=1, bias=False)
        self.bn3 = nn.BatchNorm1d(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.se_module = SEModule(planes * 4, reduction=reduction)
        self.downsample = downsample
        self.stride = stride


class SEResNetBottleneck(Bottleneck):
    """
    ResNet bottleneck with a Squeeze-and-Excitation module. It follows Caffe
    implementation and uses `stride=stride` in `conv1` and not in `conv2`
    (the latter is used in the torchvision implementation of ResNet).
    """
    expansion = 4

    def __init__(self, inplanes, planes, groups, reduction, stride=1,
                 downsample=None):
        super(SEResNetBottleneck, self).__init__()
        self.conv1 = nn.Conv1d(
            inplanes, planes, kernel_size=1, bias=False, stride=stride)
        self.bn1 = nn.BatchNorm1d(planes)
        #self.conv2 = nn.Conv1d(planes, planes, kernel_size=3, padding=1, groups=groups, bias=False)
        self.conv2 = nn.Conv1d(planes, planes, kernel_size=7, padding=3, groups=groups, bias=False)
        self.bn2 = nn.BatchNorm1d(planes)
        self.conv3 = nn.Conv1d(planes, planes * 4, kernel_size=1, bias=False)
        self.bn3 = nn.BatchNorm1d(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.se_module = SEModule(planes * 4, reduction=reduction)
        self.downsample = downsample
        self.stride = stride
        
class SEResNeXtBottleneck(Bottleneck):
    """
    ResNeXt bottleneck type C with a Squeeze-and-Excitation module.
    """
    expansion = 4

    def __init__(self, inplanes, planes, groups, reduction, stride=1, downsample=None, base_width=4):
        super(SEResNeXtBottleneck, self).__init__()
        width = math.floor(planes * (base_width / 64)) * groups
        self.conv1 = nn.Conv1d(inplanes, width, kernel_size=1, bias=False, stride=1)
        self.bn1 = nn.BatchNorm1d(width)
        #self.conv2 = nn.Conv1d(width, width, kernel_size=3, stride=stride, padding=1, groups=groups, bias=False)
        self.conv2 = nn.Conv1d(width, width, kernel_size=7, stride=stride, padding=3, groups=groups, bias=False)
        self.bn2 = nn.BatchNorm1d(width)
        self.conv3 = nn.Conv1d(width, planes * 4, kernel_size=1, bias=False)
        self.bn3 = nn.BatchNorm1d(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.se_module = SEModule(planes * 4, reduction=reduction)
        self.downsample = downsample
        self.stride = stride
        
def conv3x3(in_planes, out_planes, stride=1):
    """3x3 convolution with padding"""
    return nn.Conv1d(in_planes, out_planes, kernel_size=7, stride=stride, padding=3, bias=False)

class SEResNetBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, groups, reduction, stride=1, downsample=None):
        super(SEResNetBlock, self).__init__()
        # self.conv1 = conv3x3(inplanes, planes)
        self.conv1 = nn.Conv1d(
            inplanes, planes, kernel_size=7, padding=3, stride=stride, bias=False)

        # self.conv1 = nn.Conv1d(
        #     inplanes, planes, kernel_size=3, padding=1, stride=stride, bias=False)

        # self.bn1 = nn.BatchNorm1d(inplanes)
        self.bn1 = nn.BatchNorm1d(planes)

        # self.conv2 = conv3x3(planes, planes)

        self.conv2 = nn.Conv1d(
            planes, planes, kernel_size=7, padding=3, groups=groups, bias=False)
        
        # self.conv2 = nn.Conv1d(
        #     planes, planes, kernel_size=3, padding=1, groups=groups, bias=False)
        self.bn2 = nn.BatchNorm1d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.se_module = SEModule(planes, reduction=reduction)
        # self.se_module = SEModule(inplanes, reduction=reduction)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        #ref:https://zhuanlan.zhihu.com/p/48499356
        #ref:https://arxiv.org/abs/1512.03385
        #ref:Squeeze-and-Excitation Networks

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        # out = self.bn1(x)
        # out = self.relu(out)
        # out = self.conv1(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)
        # out = self.conv2(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out = self.se_module(out) + residual
        # out = self.se_module(residual) + out #worse
        # out = residual + out #worse
        out = self.relu(out)

        return out


class SENet(nn.Module):

    def __init__(self, block, layers, groups, reduction, drop_rate=0.2,
                 in_chans=12, inplanes=64, input_3x3=True, downsample_kernel_size=3,
                 downsample_padding=1, num_classes=9, global_pool='max'):
        """
        Parameters
        ----------
        block (nn.Module): Bottleneck class.
            - For SENet154: SEBottleneck
            - For SE-ResNet models: SEResNetBottleneck
            - For SE-ResNeXt models:  SEResNeXtBottleneck
        layers (list of ints): Number of residual blocks for 4 layers of the
            network (layer1...layer4).
        groups (int): Number of groups for the 3x3 convolution in each
            bottleneck block.
            - For SENet154: 64
            - For SE-ResNet models: 1
            - For SE-ResNeXt models:  32
        reduction (int): Reduction ratio for Squeeze-and-Excitation modules.
            - For all models: 16
        dropout_p (float or None): Drop probability for the Dropout layer.
            If `None` the Dropout layer is not used.
            - For SENet154: 0.2
            - For SE-ResNet models: None
            - For SE-ResNeXt models: None
        inplanes (int):  Number of input channels for layer1.
            - For SENet154: 128
            - For SE-ResNet models: 64
            - For SE-ResNeXt models: 64
        input_3x3 (bool): If `True`, use three 3x3 convolutions instead of
            a single 7x7 convolution in layer0.
            - For SENet154: True
            - For SE-ResNet models: False
            - For SE-ResNeXt models: False
        downsample_kernel_size (int): Kernel size for downsampling convolutions
            in layer2, layer3 and layer4.
            - For SENet154: 3
            - For SE-ResNet models: 1
            - For SE-ResNeXt models: 1
        downsample_padding (int): Padding for downsampling convolutions in
            layer2, layer3 and layer4.
            - For SENet154: 1
            - For SE-ResNet models: 0
            - For SE-ResNeXt models: 0
        num_classes (int): Number of outputs in `last_linear` layer.
            - For all models: 1000
        """
        super(SENet, self).__init__()
        self.inplanes = inplanes
        self.num_classes = num_classes
        self.drop_rate = drop_rate
        if input_3x3:
            layer0_modules = [
                ('conv1', nn.Conv1d(in_chans, 64, 3, stride=2, padding=1, bias=False)),
                ('bn1', nn.BatchNorm1d(64)),
                ('relu1', nn.ReLU(inplace=True)),
                ('conv2', nn.Conv1d(64, 64, 3, stride=1, padding=1, bias=False)),
                ('bn2', nn.BatchNorm1d(64)),
                ('relu2', nn.ReLU(inplace=True)),
                ('conv3', nn.Conv1d(64, inplanes, 3, stride=1, padding=1, bias=False)),
                ('bn3', nn.BatchNorm1d(inplanes)),
                ('relu3', nn.ReLU(inplace=True)),
            ]
        else:
            layer0_modules = [
                ('conv1', nn.Conv1d(in_chans, inplanes, kernel_size=7, stride=2, padding=3, bias=False)),
                ('bn1', nn.BatchNorm1d(inplanes)),
                ('relu1', nn.ReLU(inplace=True)),
            ]
        # To preserve compatibility with Caffe weights `ceil_mode=True`
        # is used instead of `padding=1`.
        layer0_modules.append(('pool', nn.MaxPool1d(3, stride=2, ceil_mode=True)))
        self.layer0 = nn.Sequential(OrderedDict(layer0_modules))
        self.layer1 = self._make_layer(
            block,
            planes=64,
            blocks=layers[0],
            groups=groups,
            reduction=reduction,
            downsample_kernel_size=1,
            downsample_padding=0
        )
        self.layer2 = self._make_layer(
            block,
            planes=128,
            blocks=layers[1],
            stride=2,
            groups=groups,
            reduction=reduction,
            downsample_kernel_size=downsample_kernel_size,
            downsample_padding=downsample_padding
        )
        self.layer3 = self._make_layer(
            block,
            planes=256,
            blocks=layers[2],
            stride=2,
            groups=groups,
            reduction=reduction,
            downsample_kernel_size=downsample_kernel_size,
            downsample_padding=downsample_padding
        )
        self.layer4 = self._make_layer(
            block,
            planes=512,
            blocks=layers[3],
            stride=2,
            groups=groups,
            reduction=reduction,
            downsample_kernel_size=downsample_kernel_size,
            downsample_padding=downsample_padding
        )
        self.avg_pool = SelectAdaptivePool1d(pool_type=global_pool)
        self.num_features = 512 * block.expansion
        
        self.highway_number = 2 #6:0.825;4:0.823;4:0.820
        
        self.highway_layers = nn.ModuleList([HighwayMLP(self.num_features, 
                                                        activation_function=F.relu,
                                                        gate_activation=F.sigmoid)
                                             for _ in range(self.highway_number)])

        #self.last_linear = nn.Linear(self.num_features, num_classes)
        self.last_linear = nn.Linear(256, 9)
        
        self.lstm = nn.LSTM(400, 128, bidirectional=True, batch_first=True)
        self.gru = nn.GRU(400, 128, bidirectional=True, batch_first=True)   
        self.attention_layer = Attention(128*2, 2048)
        
        for m in self.modules():
            _weight_init(m)

    def _make_layer(self, block, planes, blocks, groups, reduction, stride=1,
                    downsample_kernel_size=1, downsample_padding=0):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv1d(self.inplanes, planes * block.expansion,
                          kernel_size=downsample_kernel_size, stride=stride,
                          padding=downsample_padding, bias=False),
                nn.BatchNorm1d(planes * block.expansion),
            )

        layers = [block(
            self.inplanes, planes, groups, reduction, stride, downsample)]
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes, groups, reduction))

        return nn.Sequential(*layers)

    def get_classifier(self):
        return self.last_linear

    def reset_classifier(self, num_classes, global_pool='avg'):
        self.num_classes = num_classes
        self.avg_pool = SelectAdaptivePool1d(pool_type=global_pool)
        del self.last_linear
        if num_classes:
            self.last_linear = nn.Linear(self.num_features * self.avg_pool.feat_mult(), num_classes)
        else:
            self.last_linear = None

    def forward_features(self, x):
        x = self.layer0(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        return x

    def logits(self, x):
#         x = self.avg_pool(x).flatten(1)
#         if self.drop_rate > 0.:
#             x = F.dropout(x, p=self.drop_rate, training=self.training)
        
#         for current_layer in self.highway_layers:
#             x = current_layer(x)
                   
#         x = self.last_linear(x)
        x, _ = self.gru(x)
        x = self.attention_layer(x)
        x = self.last_linear(x)
        return x

    def forward(self, x):
        x = self.forward_features(x)
        x = self.logits(x)
        return x

def seresnet18(pretrained=False, num_classes=9, in_chans=3, **kwargs):
    model = SENet(SEResNetBlock, [2, 2, 2, 2], groups=1, reduction=16,
                  inplanes=64, input_3x3=False,
                  downsample_kernel_size=1, downsample_padding=0,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)

    return model

def seresnet34(pretrained=False, num_classes=9, in_chans=12, **kwargs):

    model = SENet(SEResNetBlock, [3, 4, 6, 3], groups=1, reduction=16,
                  inplanes=64, input_3x3=False,
                  downsample_kernel_size=1, downsample_padding=0,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)
    return model

def seresnet50(pretrained=False, num_classes=9, in_chans=12, **kwargs):

    model = SENet(SEResNetBottleneck, [3, 4, 6, 3], groups=1, reduction=16,
                  inplanes=64, input_3x3=False,
                  downsample_kernel_size=1, downsample_padding=0,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)

    return model


def seresnet101(pretrained=False, num_classes=9, in_chans=12, **kwargs):
    model = SENet(SEResNetBottleneck, [3, 4, 23, 3], groups=1, reduction=16,
                  inplanes=64, input_3x3=False, drop_rate=0.5,
                  downsample_kernel_size=1, downsample_padding=0,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)
    return model

def seresnet152(pretrained=False, num_classes=9, in_chans=12, **kwargs):
    model = SENet(SEResNetBottleneck, [3, 8, 36, 3], groups=1, reduction=16,
                  inplanes=64, input_3x3=False, drop_rate=0.5,
                  downsample_kernel_size=1, downsample_padding=0,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)
    return model


def senet154(pretrained=False, num_classes=1000, in_chans=12, **kwargs):
    # default_cfg = default_cfgs['senet154']
    model = SENet(SEBottleneck, [3, 8, 36, 3], groups=64, reduction=16,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)
    # model.default_cfg = default_cfg
    # if pretrained:
    #     load_pretrained(model, default_cfg, num_classes, in_chans)
    return model


def seresnext26_32x4d(pretrained=False, num_classes=9, in_chans=12, **kwargs):
    #default_cfg = default_cfgs['seresnext26_32x4d']
    model = SENet(SEResNeXtBottleneck, [2, 2, 2, 2], groups=32, reduction=16,
                  inplanes=64, input_3x3=False,
                  downsample_kernel_size=1, downsample_padding=0,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)
    #model.default_cfg = default_cfg
    #if pretrained:
    #    load_pretrained(model, default_cfg, num_classes, in_chans)
    return model


def seresnext50_32x4d(pretrained=False, num_classes=9, in_chans=12, **kwargs):
    #default_cfg = default_cfgs['seresnext50_32x4d']
    model = SENet(SEResNeXtBottleneck, [3, 4, 6, 3], groups=32, reduction=16,
                  inplanes=64, input_3x3=False, drop_rate=0.5,
                  downsample_kernel_size=1, downsample_padding=0,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)
    #model.default_cfg = default_cfg
    #if pretrained:
    #    load_pretrained(model, default_cfg, num_classes, in_chans)
    return model


def seresnext101_32x4d(pretrained=False, num_classes=9, in_chans=12, **kwargs):
    #default_cfg = default_cfgs['seresnext101_32x4d']
    model = SENet(SEResNeXtBottleneck, [3, 4, 23, 3], groups=32, reduction=16,
                  inplanes=64, input_3x3=False, drop_rate=0.5,
                  downsample_kernel_size=1, downsample_padding=0,
                  num_classes=num_classes, in_chans=in_chans, **kwargs)
    #model.default_cfg = default_cfg
    #if pretrained:
    #    load_pretrained(model, default_cfg, num_classes, in_chans)
    return model

if __name__ == '__main__':
    import torch

    x = torch.randn(1, 12,2560*6)
    m = seresnet50(global_pool='max')
    print(m)
    print(m(x))