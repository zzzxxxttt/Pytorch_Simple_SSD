# -*- coding: utf-8 -*-
import numpy as np


def xywh_to_xyxy_np(boxes):
  return np.concatenate((boxes[:, :2] - boxes[:, 2:] / 2,  # xmin, ymin
                         boxes[:, :2] + boxes[:, 2:] / 2), 1)  # xmax, ymax


def xyxy_to_xywh_np(boxes):
  return np.concatenate(((boxes[:, 2:] + boxes[:, :2]) / 2,  # cx, cy
                         boxes[:, 2:] - boxes[:, :2]), 1)  # w, h


def IoU_np(box_a, box_b):
  max_xy = np.minimum(box_a[:, 2:][:, None, :], box_b[:, 2:][None, :, :])
  min_xy = np.maximum(box_a[:, :2][:, None, :], box_b[:, :2][None, :, :])
  inter = np.clip((max_xy - min_xy), a_min=0, a_max=None)
  inter = inter[:, :, 0] * inter[:, :, 1]

  area_a = ((box_a[:, 2] - box_a[:, 0]) * (box_a[:, 3] - box_a[:, 1]))[:, None]  # [A,B]
  area_b = ((box_b[:, 2] - box_b[:, 0]) * (box_b[:, 3] - box_b[:, 1]))[None, :]  # [A,B]
  union = area_a + area_b - inter
  return inter / union  # [A,B]


def match_np(pos_threshold, ground_truths, priors_boxes, labels):
  # jaccard index
  overlaps = IoU_np(ground_truths, xywh_to_xyxy_np(priors_boxes))

  # [1,num_objects] best prior for each ground truth
  # best_prior_overlap = np.amax(overlaps, 1)
  best_prior_idx = np.argmax(overlaps, 1)

  # [1,num_priors] best ground truth for each prior
  best_gt_overlap = np.amax(overlaps, 0)
  best_gt_idx = np.argmax(overlaps, 0)

  # 把每个gtbox对应的最大prior的iou设置为2？
  best_gt_overlap[best_prior_idx] = 2

  # 先把每个gtbox的最大prior设置为gtbox的idx
  # ensure every gt matches with its prior of max overlap
  best_gt_idx[best_prior_idx] = np.arange(best_prior_idx.shape[0])

  # 选出每个prior对应的gtbox
  matches = ground_truths[best_gt_idx]  # Shape: [num_priors,4]

  # 选出每个prior对应的label
  cls = labels[best_gt_idx] + 1  # Shape: [num_priors]
  # iou过低的prior label是0
  # cls[best_gt_overlap < pos_threshold] = -1  # label as notuse
  cls[best_gt_overlap < pos_threshold] = 0  # label as background

  # dist b/t match center and prior's center
  g_cxcy = (matches[:, :2] + matches[:, 2:]) / 2 - priors_boxes[:, :2]
  # encode variance
  g_cxcy /= (priors_boxes[:, 2:] * 0.1)
  # match wh / prior wh
  g_wh = (matches[:, 2:] - matches[:, :2]) / priors_boxes[:, 2:]
  g_wh = np.log(g_wh) / 0.2
  # return target for smooth_l1_loss
  reg = np.concatenate([g_cxcy, g_wh], 1)  # [num_priors,4]

  return reg, cls


# Adapted from https://github.com/Hakuyume/chainer-ssd
def decode_np(loc, priors):
  boxes = np.concatenate((priors[:, :2] + loc[:, :2] * 0.1 * priors[:, 2:],
                          priors[:, 2:] * np.exp(loc[:, 2:] * 0.2)), 1)
  boxes[:, :2] -= boxes[:, 2:] / 2
  boxes[:, 2:] += boxes[:, :2]
  return boxes


def decode_batch_np(loc, priors):
  boxes = np.concatenate((priors[:, :, :2] + loc[:, :, :2] * 0.1 * priors[:, :, 2:],
                          priors[:, :, 2:] * np.exp(loc[:, :, 2:] * 0.2)), 1)
  boxes[:, :, :2] -= boxes[:, :, 2:] / 2
  boxes[:, :, 2:] += boxes[:, :, :2]
  return boxes


def detect_np(boxes_pred, probs_pred, top_k, cls_thresh, nms_thresh):
  '''
  :param boxes_pred: [batch_size, 8732, 4]
  :param probs_pred: [batch_size, 8732, 21]
  :param top_k:
  :param cls_thresh:
  :param nms_thresh:
  :return:
  '''
  batch_size = probs_pred.shape[0]
  num_classes = probs_pred.shape[2]

  max_probs = np.amax(probs_pred, axis=-1)
  argmax_probs = np.argmax(probs_pred, axis=-1)

  batch_result = []
  for i in range(batch_size):
    result = []

    prob_filter = np.logical_and(max_probs[i] > cls_thresh, argmax_probs[i] > 0)
    if np.sum(prob_filter) < 1:
      batch_result.append(None)
      continue
    boxes = boxes_pred[i, prob_filter, :]
    clses = argmax_probs[i, prob_filter]
    probs = max_probs[i, prob_filter]

    for c in range(1, num_classes):
      # 找出等于当前类的anchor

      cls_filter = clses == c
      if np.sum(cls_filter) < 1:
        continue

      # idx of highest scoring and non-overlapping boxes per class
      # 非极大值抑制
      nms_boxes, nms_probs, count = nms_np(boxes[cls_filter, :], probs[cls_filter], nms_thresh, top_k)
      # 拿出抑制之后的bbox和概率
      # output[i, cls, :count] = torch.cat((scores[nms_idx[:count]].unsqueeze(1), boxes[nms_idx[:count]]), 1)
      result.append(np.hstack([np.clip(nms_boxes, 0.0, 1.0), nms_probs[:, None], np.ones([count, 1]) * (c - 1)]))
    batch_result.append(np.vstack(result))

  return batch_result


def nms_np(boxes, scores, overlap=0.5, top_k=200):
  # 如果没有box就返回全0
  if boxes.shape[0] <= 1:
    return boxes, scores, boxes.shape[0]

  area = np.prod(boxes[:, 2:] - boxes[:, :2], axis=-1)
  argsort_prob = np.argsort(scores)[::-1]

  keep = []
  count = 0
  while argsort_prob.shape[0] > 0:
    i = argsort_prob[-1]  # index of current largest val
    # keep.append(i)
    keep.append(i)
    count += 1
    if argsort_prob.shape[0] == 1:
      break
    argsort_prob = argsort_prob[:-1]  # remove kept element from view
    # load bboxes of next highest vals
    # 除了当前box之外其他的box
    max_xy = np.minimum(boxes[i, 2:][None, :], boxes[argsort_prob, 2:])
    min_xy = np.maximum(boxes[i, :2][None, :], boxes[argsort_prob, :2])
    inter = np.clip((max_xy - min_xy), a_min=0, a_max=None)
    inter = inter[:, 0] * inter[:, 1]

    # IoU = i / (area(a) + area(b) - i)
    rem_areas = area[argsort_prob]  # load remaining areas)
    union = (rem_areas - inter) + area[i]
    IoU = inter / union  # store result in iou
    # keep only elements with an IoU <= overlap
    argsort_prob = argsort_prob[IoU < overlap]
  return boxes[keep, :], scores[keep], count


if __name__ == '__main__':
  from nets.anchors import *
  import pickle

  with open('../debug.pickle', 'rb') as handle:
    debug = pickle.load(handle)

  for i in range(len(debug)):
    out = detect_np(debug[i][0], debug[i][1], 200, 0.01, 0.45)