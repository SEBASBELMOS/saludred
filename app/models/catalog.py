"""Clinical profiles that drive the synthetic data generator.

A profile is a *label*: it says what kind of patient this is. The generator
reads the targets below and produces vital signs around them, so the records
of a diabetic patient look different from those of a healthy one without
either group being perfectly separable.

That overlap is deliberate. Data where every group sits in its own corner
makes clustering trivial and proves nothing. Real cohorts blur at the edges,
and the profile stored on the patient is the ground truth used afterwards to
check whether an automatic grouping recovered the structure.

LOINC codes and units live in ``app.models.loinc`` — this module never
redefines them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import ClinicalProfile


@dataclass(frozen=True)
class ProfileTargets:
    """Reference centres for one profile.

    ``diastolic_ratio`` is a fraction of the systolic value rather than an
    absolute number: that is what keeps systolic > diastolic in every single
    generated row without needing to check it afterwards.
    """

    systolic: float
    diastolic_ratio: float
    heart_rate: float
    respiratory_rate: float
    temperature: float
    glucose: float
    spo2: float
    bmi: float
    age_range: tuple[int, int]
    # Share of this profile's encounters that arrive as an emergency.
    emergency_rate: float


PROFILE_TARGETS: dict[ClinicalProfile, ProfileTargets] = {
    ClinicalProfile.HEALTHY: ProfileTargets(
        systolic=118, diastolic_ratio=0.63, heart_rate=72, respiratory_rate=15,
        temperature=36.6, glucose=92, spo2=98, bmi=23.5,
        age_range=(18, 55), emergency_rate=0.10,
    ),
    ClinicalProfile.HYPERTENSIVE: ProfileTargets(
        systolic=152, diastolic_ratio=0.62, heart_rate=78, respiratory_rate=16,
        temperature=36.7, glucose=98, spo2=96, bmi=29.0,
        age_range=(40, 80), emergency_rate=0.20,
    ),
    ClinicalProfile.DIABETIC: ProfileTargets(
        systolic=134, diastolic_ratio=0.62, heart_rate=80, respiratory_rate=17,
        temperature=36.8, glucose=168, spo2=96, bmi=31.0,
        age_range=(35, 78), emergency_rate=0.22,
    ),
    ClinicalProfile.CARDIAC: ProfileTargets(
        systolic=128, diastolic_ratio=0.60, heart_rate=96, respiratory_rate=21,
        temperature=36.9, glucose=104, spo2=93, bmi=27.5,
        age_range=(50, 88), emergency_rate=0.45,
    ),
    ClinicalProfile.RESPIRATORY: ProfileTargets(
        systolic=122, diastolic_ratio=0.63, heart_rate=92, respiratory_rate=26,
        temperature=37.6, glucose=96, spo2=88, bmi=25.0,
        age_range=(25, 82), emergency_rate=0.40,
    ),
    ClinicalProfile.ELDERLY_FRAIL: ProfileTargets(
        systolic=140, diastolic_ratio=0.58, heart_rate=84, respiratory_rate=19,
        temperature=36.4, glucose=112, spo2=92, bmi=21.0,
        age_range=(72, 95), emergency_rate=0.38,
    ),
}


@dataclass(frozen=True)
class ImagingOrder:
    modality: str
    body_site: str
    description: str


# Studies that make clinical sense for each profile. Handing out modalities at
# random produces data that does not survive a second look: nobody orders a
# chest MRI because of a high glucose reading.
PROFILE_IMAGING: dict[ClinicalProfile, tuple[ImagingOrder, ...]] = {
    ClinicalProfile.HEALTHY: (
        ImagingOrder("DX", "Torax", "Radiografia de torax de control"),
    ),
    ClinicalProfile.HYPERTENSIVE: (
        ImagingOrder("US", "Abdomen", "Ecografia renal"),
        ImagingOrder("DX", "Torax", "Radiografia de torax"),
    ),
    ClinicalProfile.DIABETIC: (
        ImagingOrder("US", "Abdomen", "Ecografia abdominal"),
        ImagingOrder("DX", "Pie", "Radiografia de pie diabetico"),
    ),
    ClinicalProfile.CARDIAC: (
        ImagingOrder("US", "Corazon", "Ecocardiograma transtoracico"),
        ImagingOrder("CT", "Torax", "Angiotomografia coronaria"),
        ImagingOrder("DX", "Torax", "Radiografia de torax"),
    ),
    ClinicalProfile.RESPIRATORY: (
        ImagingOrder("CT", "Torax", "Tomografia de torax de alta resolucion"),
        ImagingOrder("DX", "Torax", "Radiografia de torax"),
    ),
    ClinicalProfile.ELDERLY_FRAIL: (
        ImagingOrder("DX", "Cadera", "Radiografia de cadera"),
        ImagingOrder("CT", "Craneo", "Tomografia de craneo simple"),
    ),
}

# Series and instances per modality: a plain radiograph is one or two images,
# a CT is hundreds. The generator uses this so series_count/instance_count are
# not arbitrary numbers.
MODALITY_VOLUME: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "DX": ((1, 2), (1, 3)),
    "CR": ((1, 2), (1, 3)),
    "US": ((1, 3), (8, 40)),
    "CT": ((2, 5), (120, 480)),
    "MR": ((4, 9), (90, 320)),
    "XA": ((1, 4), (30, 150)),
    "NM": ((1, 3), (20, 90)),
}
