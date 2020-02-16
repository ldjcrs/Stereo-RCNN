# --------------------------------------------------------
# Fast R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick
# --------------------------------------------------------

"""Blob helper functions."""

import numpy as np
# from scipy.misc import imread, imresize
import cv2

try:
    xrange          # Python 2
except NameError:
    xrange = range  # Python 3


def im_list_to_blob(imgs_left, imgs_right):
    """Convert a list of images into a network input.

    Assumes images are already prepared (means subtracted, BGR order, ...).
    """
    max_shape = np.array([im.shape for im in imgs_left]).max(axis=0)
    num_images = len(imgs_left)
    blob_left = np.zeros((num_images, max_shape[0], max_shape[1], 3),
                    dtype=np.float32)
    blob_right = np.zeros((num_images, max_shape[0], max_shape[1], 3),
                    dtype=np.float32)
    for i in xrange(num_images):
        im_l = imgs_left[i]
        im_r = imgs_right[i]
        blob_left[i, 0:im_l.shape[0], 0:im_l.shape[1], :] = im_l
        blob_right[i, 0:im_r.shape[0], 0:im_r.shape[1], :] = im_r

    return blob_left, blob_right

def noisy(image):
    row,col,ch= image.shape
    mean = 0
    sigma = 2.5
    gauss = np.random.normal(mean,sigma,(row,col,ch))
    gauss = gauss.reshape(row,col,ch)
    noisy = image + gauss
    return noisy

def prep_im_for_blob(img_left, img_right, pixel_means, target_size, max_size, TRAIN=False):
    """Mean subtract and scale an image for use in a blob."""

    img_left = img_left.astype(np.float32, copy=False)
    img_left -= pixel_means

    img_right = img_right.astype(np.float32, copy=False)
    img_right -= pixel_means
    
    if TRAIN:
        img_left = noisy(img_left)
        img_right = noisy(img_right)

    im_shape = img_left.shape
    im_size_min = np.min(im_shape[0:2])
    im_size_max = np.max(im_shape[0:2])
    im_scale = float(target_size) / float(im_size_min)
    # Prevent the biggest axis from being more than MAX_SIZE
    # if np.round(im_scale * im_size_max) > max_size:
    #     im_scale = float(max_size) / float(im_size_max)
    # im = imresize(im, im_scale)
    img_left = cv2.resize(img_left, None, None, fx=im_scale, fy=im_scale,
                    interpolation=cv2.INTER_LINEAR)
    img_right = cv2.resize(img_right, None, None, fx=im_scale, fy=im_scale,
                    interpolation=cv2.INTER_LINEAR)
    if img_left.shape[1] < max_size:
        img_left = np.pad(img_left, ((0,0), (0, max_size-img_left.shape[1]), (0,0)), 'constant')
        img_right = np.pad(img_right, ((0,0), (0, max_size-img_left.shape[1]), (0,0)), 'constant')
    elif img_left.shape[1] > max_size:
        img_left = img_left[:,:max_size,:]
        img_right = img_right[:,:max_size,:]
    return img_left, img_right, im_scale
