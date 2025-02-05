"""
========================================
Using Subject Space ROIs from Freesurfer
========================================

An example using the AFQ API to find bundles
as defined by endpoint ROIs from freesurfer.
This example can be modified to work with ROIs
in subject space from pipelines other than freesurfer.
"""
import os.path as op

import nibabel as nib
import plotly
import numpy as np

from AFQ.api.group import GroupAFQ
import AFQ.data.fetch as afd
from AFQ.definitions.image import RoiImage
import AFQ.api.bundle_dict as abd

##########################################################################
# Get some example data
# ---------------------
#
# Retrieves High angular resolution diffusion imaging (HARDI) dataset from
# Stanford's Vista Lab
#
#   see https://purl.stanford.edu/ng782rw8378 for details on dataset.
#
# The data for the first subject and first session are downloaded locally
# (by default into the users home directory) under:
#
#   ``.dipy/stanford_hardi/``
#
# Anatomical data (``anat``) and Diffusion-weighted imaging data (``dwi``) are
# then extracted, formatted to be BIDS compliant, and placed in the AFQ
# data directory (by default in the users home directory) under:
#
#   ``AFQ_data/stanford_hardi/``
#
# This data represents the required preprocessed diffusion data necessary for
# intializing the GroupAFQ object (which we will do next)
#
# The clear_previous_afq is used to remove any previous runs of the afq object
# stored in the AFQ_data/stanford_hardi/ BIDS directory. Set it to None if
# you want to use the results of previous runs. Setting it to "track"
# as here will only clear derivatives that depend on the tractography stage
# (i.e., bundle delination and tract profile calculation),
# as well as the tractography itself, to save time on recomputation.
# If you want to only clear derivatives that depend on bundle delineation,
# and keep the tractography, you can set clear_previous_afq to
# "recog" instead.

afd.organize_stanford_data(clear_previous_afq="track")

##########################################################################
# Generate left thalamus ROI from freesurfer segmentation file
# ------------------------------------------------------------
# 1. Load the segmentation file that was generated by Freesurfer for
#    the specific subject.
# 2. Identify the left thalamus within the file, which has the label
#    number 41
# 3. Create a Nifti image representing the left thalamus ROI:
#    - Assign a value of 1 to the voxels that Freesurfer
#      has labeled as 41 (i.e., the left thalamus).
#    - Assign a value of 0 to all other voxels.
# This binary mask format is the expected input for pyAFQ when
# dealing with subject space ROIs. If it's already in binary format,
# there is no need to do this step.

freesurfer_subject_folder = op.join(
    afd.afq_home, "stanford_hardi",
    "derivatives", "freesurfer",
    "sub-01", "ses-01",
    "anat")

seg_file = nib.load(op.join(
    freesurfer_subject_folder, "sub-01_ses-01_seg.nii.gz"))
left_thal = seg_file.get_fdata() == 41
nib.save(
    nib.Nifti1Image(
        left_thal.astype(np.float32),
        seg_file.affine),
    op.join(
        freesurfer_subject_folder,
        "sub-01_ses-01_desc-leftThal_mask.nii.gz"))

##########################################################################
# Set tractography parameters (optional)
# ---------------------
# We make this tracking_params which we will pass to the GroupAFQ object
# which specifies that we want 10,000 seeds randomly distributed
# only within the endpoint ROIs and not throughout the white matter.
# This is controlled by passing
# `"seed_mask": RoiImage()` in the `tracking_params` dict.
#
# We only do this to make this example faster and consume less space.

tracking_params = dict(n_seeds=10000,
                       random_seeds=True,
                       rng_seed=42,
                       seed_mask=RoiImage(use_endpoints=True))

#############################################################################
# Define custom `BundleDict` object
# --------------------------------
# In a typical `BundleDict` object, ROIs are passed as paths to Nifti files.
# Here, we define ROIs as dictionaries instead, containing BIDS filters.
# Then pyAFQ can find the respective ROI for each subject and session.

bundles = abd.BundleDict({
    "L_OR": {
        "start": {
            "scope": "freesurfer",
            "suffix": "mask",
            "desc": "leftThal"},
        "end": {
            "scope": "freesurfer",
            "suffix": "anat",
            "desc": "LV1"
        },
        "cross_midline": False,
        "space": "subject"
    }})

##########################################################################
# Initialize a GroupAFQ object:
# -------------------------
#
# Creates a GroupAFQ object, that encapsulates tractometry,
# passing in our custom bundle info. Then we run the pipeline
# and generate a visualization of the bundle we found.

myafq = GroupAFQ(
    bids_path=op.join(afd.afq_home, 'stanford_hardi'),
    preproc_pipeline='vistasoft',
    tracking_params=tracking_params,
    bundle_info=bundles)

bundle_html = myafq.export("indiv_bundles_figures")
plotly.io.show(bundle_html["01"]["L_OR"])
