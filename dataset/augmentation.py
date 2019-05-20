import torch
from torchvision import transforms
import cv2
import numpy as np
import types


def intersect(box_a, box_b):
  max_xy = np.minimum(box_a[:, 2:], box_b[2:])
  min_xy = np.maximum(box_a[:, :2], box_b[:2])
  inter = np.clip((max_xy - min_xy), a_min=0, a_max=np.inf)
  return inter[:, 0] * inter[:, 1]


def jaccard_numpy(box_a, box_b):
  """Compute the jaccard overlap of two sets of boxes.  The jaccard overlap
  is simply the intersection over union of two boxes.
  E.g.:
      A ∩ B / A ∪ B = A ∩ B / (area(A) + area(B) - A ∩ B)
  Args:
      box_a: Multiple bounding boxes, Shape: [num_boxes,4]
      box_b: Single bounding box, Shape: [4]
  Return:
      jaccard overlap: Shape: [box_a.shape[0], box_a.shape[1]]
  """
  inter = intersect(box_a, box_b)
  area_a = ((box_a[:, 2] - box_a[:, 0]) *
            (box_a[:, 3] - box_a[:, 1]))  # [A,B]
  area_b = ((box_b[2] - box_b[0]) *
            (box_b[3] - box_b[1]))  # [A,B]
  union = area_a + area_b - inter
  return inter / union  # [A,B]


# 重写compose，因为对图片的变换会影响到bbox,需要一起变换
class Compose(object):
  def __init__(self, transform_list):
    self.transform_list = transform_list

  def __call__(self, img, boxes=None, labels=None):
    for t in self.transform_list:
      img, boxes, labels = t(img, boxes, labels)
    return img, boxes, labels

  def add_transform(self, transform_list):
    self.transform_list += transform_list


# class Lambda(object):
#   """Applies a lambda as a transform."""
#
#   def __init__(self, lambd):
#     assert isinstance(lambd, types.LambdaType)
#     self.lambd = lambd
#
#   def __call__(self, img, boxes=None, labels=None):
#     return self.lambd(img, boxes, labels)


class ConvertToFloat32(object):
  def __call__(self, image, boxes=None, labels=None):
    return image.astype(np.float32), boxes, labels


class ToRGB(object):
  def __init__(self, swap=(2, 1, 0)):
    self.swap = swap

  def __call__(self, image, boxes=None, labels=None):
    image = image[:, :, self.swap]
    return image, boxes, labels


class To_01(object):
  def __init__(self):
    pass

  def __call__(self, image, boxes=None, labels=None):
    image /= 255.0
    return image, boxes, labels


class Normalize(object):
  def __init__(self, mean, std):
    self.mean = np.array(mean, dtype=np.float32)
    self.std = np.array(std, dtype=np.float32)

  def __call__(self, image, boxes=None, labels=None):
    image = image.astype(np.float32)
    image -= self.mean
    image /= self.std
    return image, boxes, labels


class ToAbsoluteCoords(object):
  def __call__(self, image, boxes=None, labels=None):
    height, width, channels = image.shape
    boxes[:, 0] *= width
    boxes[:, 2] *= width
    boxes[:, 1] *= height
    boxes[:, 3] *= height
    return image, boxes, labels


class ToPercentCoords(object):
  def __call__(self, image, boxes=None, labels=None):
    height, width, channels = image.shape
    boxes[:, 0] /= width
    boxes[:, 2] /= width
    boxes[:, 1] /= height
    boxes[:, 3] /= height
    return image, boxes, labels


class Resize(object):
  def __init__(self, size=300):
    self.size = size

  def __call__(self, image, boxes=None, labels=None):
    image = cv2.resize(image, (self.size, self.size))
    return image, boxes, labels


class ToCV2Image(object):
  def __call__(self, tensor, boxes=None, labels=None):
    return tensor.cpu().numpy().astype(np.float32).transpose((1, 2, 0)), boxes, labels


class ToTensor(object):
  def __call__(self, cvimage, boxes=None, labels=None):
    return torch.from_numpy(cvimage.astype(np.float32)).permute(2, 0, 1), boxes, labels


class RandomSampleCrop(object):
  """Crop
  Arguments:
      img (Image): the image being input during training
      boxes (Tensor): the original bounding boxes in pt form
      labels (Tensor): the class labels for each bbox
      mode (float tuple): the min and max jaccard overlaps
  Return:
      (img, boxes, classes)
          img (Image): the cropped image
          boxes (Tensor): the adjusted bounding boxes in pt form
          labels (Tensor): the class labels for each bbox
  """

  def __init__(self):
    self.sample_options = (
      # using entire original input image
      None,
      # sample a patch s.t. MIN jaccard w/ obj in .1,.3,.4,.7,.9
      (0.1, None),
      (0.3, None),
      (0.7, None),
      (0.9, None),
      # randomly sample a patch
      (None, None),
    )

  def __call__(self, image, boxes=None, labels=None):
    height, width, _ = image.shape
    while True:
      # randomly choose a mode
      mode = np.random.choice(self.sample_options)
      if mode is None:
        return image, boxes, labels

      min_iou, max_iou = mode
      if min_iou is None:
        min_iou = float('-inf')
      if max_iou is None:
        max_iou = float('inf')

      # max trails (50)
      for _ in range(50):
        current_image = image

        w = np.random.uniform(0.3 * width, width)
        h = np.random.uniform(0.3 * height, height)

        # aspect ratio constraint b/t .5 & 2
        if h / w < 0.5 or h / w > 2:
          continue

        left = np.random.uniform(width - w)
        top = np.random.uniform(height - h)

        # convert to integer rect x1,y1,x2,y2
        rect = np.array([int(left), int(top), int(left + w), int(top + h)])

        # calculate IoU (jaccard overlap) b/t the cropped and gt boxes
        overlap = jaccard_numpy(boxes, rect)

        # is min and max overlap constraint satisfied? if not try again
        if overlap.min() < min_iou and max_iou < overlap.max():
          continue

        # cut the crop from the image
        current_image = current_image[rect[1]:rect[3], rect[0]:rect[2],
                        :]

        # keep overlap with gt box IF center in sampled patch
        centers = (boxes[:, :2] + boxes[:, 2:]) / 2.0

        # mask in all gt boxes that above and to the left of centers
        m1 = (rect[0] < centers[:, 0]) * (rect[1] < centers[:, 1])

        # mask in all gt boxes that under and to the right of centers
        m2 = (rect[2] > centers[:, 0]) * (rect[3] > centers[:, 1])

        # mask in that both m1 and m2 are true
        mask = m1 * m2

        # have any valid boxes? try again if not
        if not mask.any():
          continue

        # take only matching gt boxes
        current_boxes = boxes[mask, :].copy()

        # take only matching gt labels
        current_labels = labels[mask]

        # should we use the box left and top corner or the crop's
        current_boxes[:, :2] = np.maximum(current_boxes[:, :2],
                                          rect[:2])
        # adjust to crop (by substracting crop's left,top)
        current_boxes[:, :2] -= rect[:2]

        current_boxes[:, 2:] = np.minimum(current_boxes[:, 2:],
                                          rect[2:])
        # adjust to crop (by substracting crop's left,top)
        current_boxes[:, 2:] -= rect[:2]

        return current_image, current_boxes, current_labels


class Expand(object):
  def __init__(self, mean):
    self.mean = mean

  def __call__(self, image, boxes, labels):
    if np.random.randint(2):
      return image, boxes, labels

    height, width, depth = image.shape
    ratio = np.random.uniform(1, 4)
    left = np.random.uniform(0, width * ratio - width)
    top = np.random.uniform(0, height * ratio - height)

    expand_image = np.zeros((int(height * ratio), int(width * ratio), depth), dtype=image.dtype)
    expand_image[:, :, :] = self.mean
    expand_image[int(top):int(top + height), int(left):int(left + width)] = image
    image = expand_image

    boxes = boxes.copy()
    boxes[:, :2] += (int(left), int(top))
    boxes[:, 2:] += (int(left), int(top))

    return image, boxes, labels


class RandomMirrorAbs(object):
  def __call__(self, image, boxes, labels):
    _, width, _ = image.shape
    if np.random.randint(2):
      image = image[:, ::-1]
      # boxes = boxes.copy()
      boxes[:, 0::2] = width - boxes[:, 2::-2]
    return image, boxes, labels


class RandomMirrorPct(object):
  def __call__(self, image, boxes, labels):
    _, width, _ = image.shape
    if np.random.randint(2):
      image = image[:, ::-1]
      # boxes = boxes.copy()#为啥要copy？
      boxes[:, 0::2] = 1.0 - boxes[:, 2::-2]
    return image, boxes, labels


class RandomSaturation(object):
  def __init__(self, lower=0.5, upper=1.5):
    self.lower = lower
    self.upper = upper
    assert self.upper >= self.lower, "contrast upper must be >= lower."
    assert self.lower >= 0, "contrast lower must be non-negative."

  def __call__(self, image, boxes=None, labels=None):
    if np.random.randint(2):
      image[:, :, 1] *= np.random.uniform(self.lower, self.upper)

    return image, boxes, labels


class RandomHue(object):
  def __init__(self, delta=18.0):
    assert 0.0 <= delta <= 360.0
    self.delta = delta

  def __call__(self, image, boxes=None, labels=None):
    if np.random.randint(2):
      image[:, :, 0] += np.random.uniform(-self.delta, self.delta)
      image[:, :, 0][image[:, :, 0] > 360.0] -= 360.0
      image[:, :, 0][image[:, :, 0] < 0.0] += 360.0
    return image, boxes, labels


class RandomChannelShuffle(object):
  def __init__(self):
    self.perms = ((0, 1, 2), (0, 2, 1),
                  (1, 0, 2), (1, 2, 0),
                  (2, 0, 1), (2, 1, 0))

  def __call__(self, image, boxes=None, labels=None):
    if np.random.randint(2):
      image = image[:, :, self.perms[np.random.randint(len(self.perms))]]  # shuffle channels
    return image, boxes, labels


class ConvertColor(object):
  def __init__(self, current='BGR', transform='HSV'):
    self.transform = transform
    self.current = current

  def __call__(self, image, boxes=None, labels=None):
    if self.current == 'BGR' and self.transform == 'HSV':
      image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    elif self.current == 'HSV' and self.transform == 'BGR':
      image = cv2.cvtColor(image, cv2.COLOR_HSV2BGR)
    else:
      raise NotImplementedError
    return image, boxes, labels


class RandomContrast(object):
  def __init__(self, lower=0.5, upper=1.5):
    self.lower = lower
    self.upper = upper
    assert self.upper >= self.lower, "contrast upper must be >= lower."
    assert self.lower >= 0, "contrast lower must be non-negative."

  # expects float image
  def __call__(self, image, boxes=None, labels=None):
    if np.random.randint(2):
      alpha = np.random.uniform(self.lower, self.upper)
      image *= alpha
    return image, boxes, labels


class RandomBrightness(object):
  def __init__(self, delta=32):
    assert delta >= 0.0
    assert delta <= 255.0
    self.delta = delta

  def __call__(self, image, boxes=None, labels=None):
    if np.random.randint(2):
      delta = np.random.uniform(-self.delta, self.delta)
      image += delta
    return image, boxes, labels


class PhotometricDistort(object):
  def __init__(self):
    self.pd = [RandomContrast(),  # 图片像素值随机乘以高斯系数
               ConvertColor(current='BGR', transform='HSV'),  # 图片转换为HSV空间
               RandomSaturation(),  # V通道随机乘以高斯系数
               RandomHue(),  # H通道随机乘以高斯系数
               ConvertColor(current='HSV', transform='BGR'),  # 图片转换为BGR空间
               RandomContrast()]  # 图片像素值随机乘以高斯系数

    self.rand_brightness = RandomBrightness()  # 图片像素值随机加上偏移量
    self.rand_channel_shuffle = RandomChannelShuffle()  # 随机调换图片通道

  def __call__(self, image, boxes, labels):
    # im = image.copy() #为啥要copy？
    image, boxes, labels = self.rand_brightness(image, boxes, labels)

    distort = Compose(self.pd[:-1] if np.random.randint(2) else self.pd[1:])
    image, boxes, labels = distort(image, boxes, labels)

    return self.rand_channel_shuffle(image, boxes, labels)


class imageAugmentation(object):
  def __init__(self, size=300, mean=(104, 117, 123), std=(1, 1, 1), train=True, to_01=False, to_rgb=False):
    self.mean = mean
    self.std = std
    self.size = size
    if train:
      self.augment = Compose([ConvertToFloat32(),  # 把int型图片转为float
                              ToAbsoluteCoords(),  # 把百分比box转为坐标box
                              PhotometricDistort(),  # 图片风格变换
                              Expand(self.mean),  # 图片随机扩大，扩充部分用均值填充
                              RandomSampleCrop(),  # 随机裁剪图片，保证新box的重叠率
                              # RandomMirrorPct(),  # 随机翻转图片
                              RandomMirrorAbs(),  # 随机翻转图片
                              ToPercentCoords(),  # 坐标box转换回百分比box
                              Resize(self.size)])  # 图片缩放到固定大小

    else:
      self.augment = Compose([ConvertToFloat32(),  # 把int型图片转为float
                              Resize(self.size)])  # 图片缩放到固定大小
    if to_01:
      self.augment.add_transform([To_01()])  # 图片从0-255变为0-1
    if to_rgb:
      self.augment.add_transform([ToRGB()])  # 从BGR转换为RGB
    self.augment.add_transform([Normalize(self.mean, self.std)])  # 减去均值

  def __call__(self, img, boxes, labels):
    return self.augment(img, boxes, labels)


def aug_generator(base_network, train):
  if base_network == 'vgg16':
    return imageAugmentation(train=train,
                             to_01=False, to_rgb=False, mean=[104, 117, 123], std=[1, 1, 1])
  elif base_network == 'mobilenet':
    return imageAugmentation(train=train,
                             to_01=True, to_rgb=True, mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225))
  else:
    assert False, 'base unknown !'
