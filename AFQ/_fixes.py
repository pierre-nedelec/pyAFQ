import numpy as np

from scipy.special import lpmv, gammaln

from tqdm import tqdm
from dipy.align import Bunch
from dipy.tracking.local_tracking import (LocalTracking,
                                          ParticleFilteringTracking)
from dipy.tracking.stopping_criterion import StreamlineStatus
import random

import math

from dipy.reconst.gqi import squared_radial_component
from dipy.data import default_sphere
from dipy.tracking.streamline import set_number_of_points
from scipy.linalg import blas


def gwi_odf(gqmodel, data):
    gqi_vector = np.real(
        squared_radial_component(np.dot(
            gqmodel.b_vector, default_sphere.vertices.T)
            * gqmodel.Lambda))
    odf = blas.dgemm(
        alpha=1.,
        a=data.reshape(-1, gqi_vector.shape[0]),
        b=gqi_vector
    ).reshape((*data.shape[:-1], gqi_vector.shape[1]))
    return odf


def spherical_harmonics(m, n, theta, phi):
    """
    An implementation of spherical harmonics that overcomes conda compilation
    issues. See: https://github.com/nipy/dipy/issues/852
    """
    x = np.cos(phi)
    val = lpmv(m, n, x).astype(complex)
    val *= np.sqrt((2 * n + 1) / 4.0 / np.pi)
    val *= np.exp(0.5 * (gammaln(n - m + 1) - gammaln(n + m + 1)))
    val = val * np.exp(1j * m * theta)
    return val


TissueTypes = Bunch(OUTSIDEIMAGE=-1, INVALIDPOINT=0, TRACKPOINT=1, ENDPOINT=2)


def _verbose_generate_tractogram(self):
    """A streamline generator"""

    # Get inverse transform (lin/offset) for seeds
    inv_A = np.linalg.inv(self.affine)
    lin = inv_A[:3, :3]
    offset = inv_A[:3, 3]

    F = np.empty((self.max_length + 1, 3), dtype=float)
    B = F.copy()
    for s in tqdm(self.seeds):
        s = np.dot(lin, s) + offset
        # Set the random seed in numpy and random
        if self.random_seed is not None:
            s_random_seed = hash(np.abs((np.sum(s)) + self.random_seed)) \
                % (2**32 - 1)
            random.seed(s_random_seed)
            np.random.seed(s_random_seed)
        directions = self.direction_getter.initial_direction(s)
        if directions.size == 0 and self.return_all:
            # only the seed position
            if self.save_seeds:
                yield [s], s
            else:
                yield [s]
        directions = directions[:self.max_cross]
        for first_step in directions:
            stepsF, stream_status = self._tracker(s, first_step, F)
            if not (self.return_all
                    or stream_status == StreamlineStatus.ENDPOINT
                    or stream_status == StreamlineStatus.OUTSIDEIMAGE):
                continue
            first_step = -first_step
            stepsB, stream_status = self._tracker(s, first_step, B)
            if not (self.return_all
                    or stream_status == StreamlineStatus.ENDPOINT
                    or stream_status == StreamlineStatus.OUTSIDEIMAGE):
                continue
            if stepsB == 1:
                streamline = F[:stepsF].copy()
            else:
                parts = (B[stepsB - 1:0:-1], F[:stepsF])
                streamline = np.concatenate(parts, axis=0)

            # move to the next streamline if only the seed position
            # and not return all
            len_sl = len(streamline)
            if len_sl >= self.min_length:
                if len_sl <= self.max_length:
                    if self.save_seeds:
                        yield streamline, s
                    else:
                        yield streamline


class VerboseLocalTracking(LocalTracking):
    def __init__(self, *args, min_length=10, max_length=1000, **kwargs):
        super().__init__(*args, **kwargs)
        self.min_length = min_length
        self.max_length = max_length
    _generate_tractogram = _verbose_generate_tractogram


class VerboseParticleFilteringTracking(ParticleFilteringTracking):
    def __init__(self, *args, min_length=10, max_length=1000, **kwargs):
        super().__init__(*args, **kwargs)
        self.min_length = min_length
        self.max_length = max_length
    _generate_tractogram = _verbose_generate_tractogram


def in_place_norm(vec, axis=-1, keepdims=False, delvec=True):
    """ Return Vectors with Euclidean (L2) norm

    See :term:`unit vector` and :term:`Euclidean norm`

    Parameters
    -------------
    vec : array_like
        Vectors to norm. Squared in the process of calculating the norm.
    axis : int, optional
        Axis over which to norm. By default norm over last axis. If `axis` is
        None, `vec` is flattened then normed. Default is -1.
    keepdims : bool, optional
        If True, the output will have the same number of dimensions as `vec`,
        with shape 1 on `axis`. Default is False.
    delvec : bool, optional
        If True, vec is deleted as soon as possible.
        If False, vec is not deleted, but still squared. Default is True.

    Returns
    ---------
    norm : array
        Euclidean norms of vectors.

    Examples
    --------
    >>> vec = [[8, 15, 0], [0, 36, 77]]
    >>> in_place_norm(vec)
    array([ 17.,  85.])
    >>> vec = [[8, 15, 0], [0, 36, 77]]
    >>> in_place_norm(vec, keepdims=True)
    array([[ 17.],
           [ 85.]])
    >>> vec = [[8, 15, 0], [0, 36, 77]]
    >>> in_place_norm(vec, axis=0)
    array([  8.,  39.,  77.])
    """
    vec = np.asarray(vec)

    if keepdims:
        ndim = vec.ndim
        shape = vec.shape

    np.square(vec, out=vec)
    vec_norm = vec.sum(axis)
    if delvec:
        del vec
    try:
        np.sqrt(vec_norm, out=vec_norm)
    except TypeError:
        vec_norm = vec_norm.astype(float)
        np.sqrt(vec_norm, out=vec_norm)

    if keepdims:
        if axis is None:
            shape = [1] * ndim
        else:
            shape = list(shape)
            shape[axis] = 1
        vec_norm = vec_norm.reshape(shape)

    return vec_norm


def tensor_odf(evals, evecs, sphere, num_batches=100):
    """
    Calculate the tensor Orientation Distribution Function

    Parameters
    ----------
    evals : array (4D)
        Eigenvalues of a tensor. Shape (x, y, z, 3).
    evecs : array (5D)
        Eigenvectors of a tensor. Shape (x, y, z, 3, 3)
    sphere : sphere object
        The ODF will be calculated in each vertex of this sphere.
    num_batches : int
        Split the calculation into batches. This reduces memory usage.
        If memory use is not an issue, set to 1.
        If set to -1, there will be 1 batch per vertex in the sphere.
        Default: 100
    """
    num_vertices = sphere.vertices.shape[0]
    if num_batches == -1:
        num_batches = num_vertices
    batch_size = math.ceil(num_vertices / num_batches)
    batches = range(num_batches)

    mask = np.where((evals[..., 0] > 0)
                    & (evals[..., 1] > 0)
                    & (evals[..., 2] > 0))
    evecs = evecs[mask]

    proj_norm = np.zeros((num_vertices, evecs.shape[0]))

    it = tqdm(batches) if num_batches != 1 else batches
    for i in it:
        start = i * batch_size
        end = (i + 1) * batch_size
        if end > num_vertices:
            end = num_vertices

        proj = np.dot(sphere.vertices[start:end], evecs)
        proj /= np.sqrt(evals[mask])
        proj_norm[start:end, :] = in_place_norm(proj)

    proj_norm **= -3
    proj_norm /= 4 * np.pi * np.sqrt(np.prod(evals[mask], -1))

    odf = np.zeros((evals.shape[:3] + (sphere.vertices.shape[0],)))
    odf[mask] = proj_norm.T
    return odf


def gaussian_weights(bundle, n_points=100, return_mahalnobis=False,
                     stat=np.mean):
    """
    Calculate weights for each streamline/node in a bundle, based on a
    Mahalanobis distance from the core the bundle, at that node (mean, per
    default).

    Parameters
    ----------
    bundle : Streamlines
        The streamlines to weight.
    n_points : int, optional
        The number of points to resample to. *If the `bundle` is an array,
        this input is ignored*. Default: 100.
    return_mahalanobis : bool, optional
        Whether to return the Mahalanobis distance instead of the weights.
        Default: False.
    stat : callable, optional.
        The statistic used to calculate the central tendency of streamlines in
        each node. Can be one of {`np.mean`, `np.median`} or other functions
        that have similar API. Default: `np.mean`

    Returns
    -------
    w : array of shape (n_streamlines, n_points)
        Weights for each node in each streamline, calculated as its relative
        inverse of the Mahalanobis distance, relative to the distribution of
        coordinates at that node position across streamlines.

    """

    # Resample to same length for each streamline
    # if necessary
    resample = False
    if isinstance(bundle, np.ndarray):
        if len(bundle.shape) > 2:
            if bundle.shape[1] != n_points:
                sls = bundle.tolist()
                sls = [np.asarray(item) for item in sls]
                resample = True
    else:
        resample = True
    if resample:
        sls = set_number_of_points(bundle, n_points)
    else:
        sls = bundle

    # If there's only one fiber here, it gets the entire weighting:
    if len(bundle) == 1:
        if return_mahalnobis:
            return np.array([np.nan])
        else:
            return np.array([1])

    n_sls, n_nodes, n_dim = sls.shape
    weights = np.zeros((n_sls, n_nodes))
    diff = stat(sls, axis=0) - sls
    for i in range(n_nodes):
        # This should come back as a 3D covariance matrix with the spatial
        # variance covariance of this node across the different streamlines,
        # reorganized as an upper diagonal matrix for expected Mahalanobis
        v_inv = np.triu(np.cov(sls[:, i, :].T, ddof=0))

        # calculate Mahalanobis for node in every fiber
        if np.linalg.matrix_rank(v_inv) == n_dim:
            v_inv = np.linalg.inv(v_inv)

            dist = (diff[:, i, :] @ v_inv) * diff[:, i, :]
            weights[:, i] = np.sqrt(np.sum(dist, axis=1))

        # In the special case where all the streamlines have the exact same
        # coordinate in this node, the covariance matrix is all zeros, so
        # we can't calculate the Mahalanobis distance, we will instead give
        # each streamline an identical weight, equal to the number of
        # streamlines:
        else:
            weights[:, i] = 0
    if return_mahalnobis:
        return weights

    # weighting is inverse to the distance (the further you are, the less you
    # should be weighted)
    weights = 1 / weights
    # Normalize before returning, so that the weights in each node sum to 1:
    return weights / np.sum(weights, 0)
