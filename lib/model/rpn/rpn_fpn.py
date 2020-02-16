import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable

from model.utils.config import cfg
from proposal_layer_fpn import _ProposalLayer_FPN
from anchor_target_layer_fpn import _AnchorTargetLayer_FPN
from model.utils.net_utils import _smooth_l1_loss

import numpy as np
import math
import pdb
import time

class _RPN_FPN(nn.Module):
    """ region proposal network """
    def __init__(self, din):
        super(_RPN_FPN, self).__init__()

        self.din = din  # get depth of input feature map, e.g., 512
        self.anchor_ratios = cfg.ANCHOR_RATIOS
        self.anchor_scales = cfg.ANCHOR_SCALES
        self.feat_stride = cfg.FEAT_STRIDE[0]

        # define the convrelu layers processing input feature map
        self.RPN_Conv = nn.Conv2d(self.din, 512, 3, 1, 1, bias=True)

        # define bg/fg classifcation score layer
        # self.nc_score_out = len(self.anchor_scales) * len(self.anchor_ratios) * 2 # 2(bg/fg) * 9 (anchors)
        self.nc_score_out = 1 * len(self.anchor_ratios) * 2 # 2(bg/fg) * 3 (anchor ratios) * 1 (anchor scale)
        self.RPN_cls_score = nn.Conv2d(512, self.nc_score_out, 1, 1, 0)

        # define anchor box offset prediction layer
        # self.nc_bbox_out = len(self.anchor_scales) * len(self.anchor_ratios) * 4 # 4(coords) * 9 (anchors)
        self.nc_bbox_out = 1 * len(self.anchor_ratios) * 4 # 4(coords) * 3 (anchors) * 1 (anchor scale)
        self.RPN_bbox_pred_left_right = nn.Conv2d(512, self.nc_bbox_out, 1, 1, 0)

        # define proposal layer
        self.RPN_proposal = _ProposalLayer_FPN(self.feat_stride, self.anchor_scales, self.anchor_ratios)

        # define anchor target layer
        self.RPN_anchor_target = _AnchorTargetLayer_FPN(self.feat_stride, self.anchor_scales, self.anchor_ratios)

        self.rpn_loss_cls = 0
        self.rpn_loss_box_left_right = 0

    @staticmethod
    def reshape(x, d):
        input_shape = x.size()
        x = x.view(
            input_shape[0],
            int(d),
            int(float(input_shape[1] * input_shape[2]) / float(d)),
            input_shape[3]
        )
        return x

    def forward(self, rpn_feature_maps_left, im_info, gt_boxes_left, num_boxes):        


        n_feat_maps = len(rpn_feature_maps_left)

        rpn_cls_scores = []
        rpn_cls_probs = []
        rpn_bbox_preds_left_right = []
        rpn_shapes = []

        for i in range(n_feat_maps):
            batch_size = rpn_feature_maps_left[i].size(0)
            
            # get rpn classification score
            rpn_conv1 = F.relu(self.RPN_Conv(rpn_feature_maps_left[i]), inplace=True)
            rpn_cls_score = self.RPN_cls_score(rpn_conv1)

            rpn_cls_score_reshape = self.reshape(rpn_cls_score, 2)
            rpn_cls_prob_reshape = F.softmax(rpn_cls_score_reshape, 1)
            rpn_cls_prob = self.reshape(rpn_cls_prob_reshape, self.nc_score_out)

            # get rpn offsets to the anchor boxes
            rpn_bbox_pred_left_right = self.RPN_bbox_pred_left_right(rpn_conv1)

            rpn_shapes.append([rpn_cls_score.size()[2], rpn_cls_score.size()[3]])
            rpn_cls_scores.append(rpn_cls_score.permute(0, 2, 3, 1).contiguous().view(batch_size, -1, 2))
            rpn_cls_probs.append(rpn_cls_prob.permute(0, 2, 3, 1).contiguous().view(batch_size, -1, 2))
            rpn_bbox_preds_left_right.append(rpn_bbox_pred_left_right.permute(0, 2, 3, 1).contiguous().view(batch_size, -1, 4))

        rpn_cls_score_alls = torch.cat(rpn_cls_scores, 1)
        rpn_cls_prob_alls = torch.cat(rpn_cls_probs, 1)
        rpn_bbox_pred_alls_left_right = torch.cat(rpn_bbox_preds_left_right, 1)

        n_rpn_pred = rpn_cls_score_alls.size(1)

        # proposal layer
        cfg_key = 'TRAIN' if self.training else 'TEST'

        rois_left = self.RPN_proposal((rpn_cls_prob_alls.data, rpn_bbox_pred_alls_left_right.data,
                                 im_info, cfg_key, rpn_shapes))

        self.rpn_loss_cls = 0
        self.rpn_loss_box = 0

        # generating training labels and build the rpn loss
        if self.training:
            assert gt_boxes_left is not None

            rpn_data = self.RPN_anchor_target((rpn_cls_score_alls.data, gt_boxes_left, im_info, num_boxes, rpn_shapes))

            # compute classification loss
            rpn_label = rpn_data[0].view(batch_size, -1)
            rpn_keep = Variable(rpn_label.view(-1).ne(-1).nonzero().view(-1))
            rpn_cls_score = torch.index_select(rpn_cls_score_alls.view(-1,2), 0, rpn_keep)
            rpn_label = torch.index_select(rpn_label.view(-1), 0, rpn_keep.data)
            rpn_label = Variable(rpn_label.long())
            self.rpn_loss_cls = F.cross_entropy(rpn_cls_score, rpn_label)
            fg_cnt = torch.sum(rpn_label.data.ne(0))

            rpn_bbox_targets_left, rpn_bbox_inside_weights, rpn_bbox_outside_weights = rpn_data[1:]
            rpn_bbox_targets_left_right = rpn_bbox_targets_left.new(rpn_bbox_targets_left.size()[0], rpn_bbox_targets_left.size()[1],4).zero_()
            rpn_bbox_targets_left_right[:,:,:4] = rpn_bbox_targets_left
            # compute bbox regression loss
            rpn_bbox_inside_weights = Variable(rpn_bbox_inside_weights.unsqueeze(2) \
                    .expand(batch_size, rpn_bbox_inside_weights.size(1), 4))
            rpn_bbox_outside_weights = Variable(rpn_bbox_outside_weights.unsqueeze(2) \
                    .expand(batch_size, rpn_bbox_outside_weights.size(1), 4))
            rpn_bbox_targets_left_right = Variable(rpn_bbox_targets_left_right)
            
            self.rpn_loss_box_left_right = _smooth_l1_loss(rpn_bbox_pred_alls_left_right, rpn_bbox_targets_left_right, rpn_bbox_inside_weights, 
                            rpn_bbox_outside_weights, sigma=3)

        return rois_left, self.rpn_loss_cls, self.rpn_loss_box_left_right




