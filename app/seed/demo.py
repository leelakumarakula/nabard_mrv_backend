"""Synthetic DEMO data for local development and demonstrations – Version 2.0 programme scope.

Programme: "Climate-Resilient Coffee Landscapes and Inclusive Rural Livelihoods Programme" (proposal dated
29 Oct 2026). The programme record, its proposal targets (all *proposed*) and the six state names come from the
proposal; everything else – district placement, clusters, FPOs/grower groups, households, farmers, plots, devices,
records and results – is synthetic, flagged is_demo=True and labelled DEMO/SYNTHETIC in the UI. The earlier proposal's
figures (8 states, 8,000 farmers, 16,000 ha, ₹30 crore) are deliberately not loaded.
"""

import random
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, build_current_user
from app.core.security import hash_password
from app.models import (
    Advisory,
    AdvisoryDelivery,
    BaselineRecord,
    BeneficiaryCategory,
    ClusterMembership,
    CommunityResourcePerson,
    DemographicField,
    DemonstrationSite,
    Device,
    DeviceCalibration,
    EvidenceAsset,
    EvidenceLink,
    Farm,
    Farmer,
    FarmerCategoryAssignment,
    FarmPlot,
    FieldRecord,
    FieldRecordParticipant,
    GeoUnit,
    IndicatorDefinition,
    IntegrationFieldMapping,
    IntegrationSource,
    MonitoringRecord,
    Organisation,
    PestSurveillanceCluster,
    PracticeRecord,
    Programme,
    ProgrammeOrganisation,
    ProgrammeParticipation,
    ProgrammeTarget,
    ReportingPeriod,
    Role,
    SystemSetting,
    User,
    UserScope,
    VerificationFinding,
    VerificationSample,
)
from app.seed.decisions import seed_programme_decisions
from app.services import mrv, record_types
from app.services import verification as verification_service
from app.services.integration.adapters import get_adapter, with_retry
from app.services.integration.pipeline import process_batch
from app.services.storage import checksum, get_storage
from app.services.units import area_in_ha
from app.services.validation import validate_baseline, validate_field_record, validate_monitoring

IST = timezone(timedelta(hours=5, minutes=30))
PROGRAMME_CODE = "CCRL-INCLUSIVE"
PROPOSAL_TITLE = "Climate-Resilient Coffee Landscapes and Inclusive Rural Livelihoods Programme"
SOURCE_NOTE = (f"{PROPOSAL_TITLE}, proposal dated 29 Oct 2026 – proposal figure; requires programme-owner approval. "
               "Not sanctioned or achieved.")

# Six states per Version 2.0 §1 (spec text). The architecture diagram lists Odisha instead of Meghalaya – pending confirmation.
STATES = [("AP", "Andhra Pradesh"), ("NL", "Nagaland"), ("AR", "Arunachal Pradesh"), ("MZ", "Mizoram"),
          ("MN", "Manipur"), ("ML", "Meghalaya")]
# Illustrative district placement for demo data only (final district/cluster master lists are pending, V2 §15).
DISTRICTS = {"AP": ["Alluri Sitharama Raju", "Anakapalli"], "NL": ["Kohima"], "AR": ["Papum Pare"], "MZ": ["Aizawl"],
             "MN": ["Ukhrul"], "ML": ["East Khasi Hills"]}
NORTH_EAST = {"NL", "AR", "MZ", "MN", "ML"}

TARGETS = [  # metric_code, name, value, unit, linked indicator code
    ("HOUSEHOLDS", "Households (proposed/target)", 6500, "households", None),
    ("COFFEE_AREA_HA", "Existing coffee area", 3000, "ha", None),
    ("STATES", "States covered", 6, "states", None),
    ("DEMO_SITES_PLANTATION", "Plantation demonstration sites", 60, "sites", "DEMO_SITES_PLANTATION"),
    ("DEMO_SITES_SOIL_MOISTURE", "Soil/moisture demonstration sites", 60, "sites", "DEMO_SITES_SOIL_MOISTURE"),
    ("PEST_SURVEILLANCE_CLUSTERS", "Pest surveillance clusters", 30, "clusters", "PEST_SURVEILLANCE_CLUSTERS_ACTIVE"),
    ("HH_CLIMATE_RESILIENT_ADOPTION", "Households adopting climate-resilient practices", 4725, "households",
     "HH_CLIMATE_RESILIENT_ADOPTION"),
    ("HH_SOIL_MOISTURE_CONSERVATION", "Households implementing soil and moisture conservation", 4000, "households",
     "HH_SOIL_MOISTURE_CONSERVATION"),
    ("HH_PEST_SURVEILLANCE", "Households participating in pest surveillance", 3000, "households", "HH_PEST_SURVEILLANCE"),
    ("WOMEN_BENEFICIARIES", "Women beneficiaries", 2600, "women", "WOMEN_BENEFICIARIES"),
    ("WOMEN_ADOPTING", "Women adopting adaptation practices", 1600, "women", "WOMEN_ADOPTING"),
    ("CRPS_TRAINED", "Community resource persons trained", 100, "persons", "CRPS_TRAINED"),
    ("CRPS_WOMEN_TRAINED", "Women among community resource persons (at least)", 50, "persons", "CRPS_WOMEN_TRAINED"),
    ("IOT_DEVICES", "IoT devices (planning ceiling – confirm final equipment schedule)", 1200, "devices", None),
    ("OUTLAY_CR", "Indicative outlay (not sanctioned)", 15, "INR crore", None),
]

PENDING = "Pending programme-owner approval."
# code, name, family, module, unit, method, params, definition, limitations
INDICATORS = [
    ("HH_CLIMATE_RESILIENT_ADOPTION", "Households adopting climate-resilient practices", "Climate-resilient adoption",
     "mrv", "households", "households_adopting", {"activity_codes": record_types.CLIMATE_RESILIENT_ACTIVITIES},
     "Unique eligible households with qualifying, field-observed adoption of at least one climate-resilient practice.",
     "Qualifying practice list, eligibility and verification rule require approval."),
    ("HH_SOIL_MOISTURE_CONSERVATION", "Households implementing soil and moisture conservation", "Soil & water conservation",
     "water", "households", "households_adopting", {"activity_codes": record_types.CONSERVATION_ACTIVITIES},
     "Unique households with field-observed soil and moisture conservation practices (mulching, ground cover, contour "
     "measures, erosion control, moisture retention).", "Assessment period and evidence rule require approval."),
    ("HH_PEST_SURVEILLANCE", "Households participating in pest surveillance", "Pest/disease resilience", "pest",
     "households", "households_in_surveillance", {},
     "Unique households with an active membership in a pest surveillance cluster.",
     "Participation definition (membership vs. inspection) requires approval."),
    ("WOMEN_BENEFICIARIES", "Women beneficiaries", "Inclusion", "farmer_fpo", "women", "category_beneficiaries",
     {"category_code": "WOMAN_BENEFICIARY"},
     "Enrolled farmers recorded as programme-defined women beneficiaries.", "Definition of 'woman beneficiary' pending."),
    ("WOMEN_ADOPTING", "Women adopting adaptation practices", "Inclusion", "farmer_fpo", "women", "category_adopting",
     {"category_code": "WOMAN_BENEFICIARY", "activity_codes": record_types.CLIMATE_RESILIENT_ACTIVITIES},
     "Women beneficiaries with qualifying, field-observed adoption. Not inferred from training or advisory receipt.", None),
    ("CRPS_TRAINED", "Community resource persons trained", "Institutional capacity", "farmer_fpo", "persons",
     "crps_trained", {}, "CRPs who completed training.", "Attendance/competency evidence rule pending."),
    ("CRPS_WOMEN_TRAINED", "Women community resource persons trained", "Inclusion", "farmer_fpo", "persons",
     "crps_trained", {"women_only": True}, "Women CRPs who completed training.", None),
    ("DEMO_SITES_PLANTATION", "Plantation demonstration sites established", "Demonstrations", "farmer_fpo", "sites",
     "demonstration_sites_active", {"category": "plantation"}, "Plantation demonstration sites established or active.", None),
    ("DEMO_SITES_SOIL_MOISTURE", "Soil/moisture demonstration sites established", "Demonstrations", "water", "sites",
     "demonstration_sites_active", {"category": "soil_moisture"}, "Soil/moisture demonstration sites established or active.",
     None),
    ("PEST_SURVEILLANCE_CLUSTERS_ACTIVE", "Active pest surveillance clusters", "Pest/disease resilience", "pest", "clusters",
     "surveillance_clusters_active", {}, "Pest surveillance clusters with status active.", None),
    ("CLIM_FARMERS_REACHED_ALERTS", "Farmers reached by climate alerts", "Climate preparedness", "climate", "farmers",
     "farmers_reached", {"advisory_type": "alert", "origin_module": "climate"},
     "Distinct farmers with a confirmed delivery of a climate alert in the period.", "Delivery is not adoption."),
    ("CLIM_ADVISORY_RESPONSE_RATE", "Climate advisory response rate", "Climate preparedness", "climate", "%",
     "advisory_response_rate", {"origin_module": "climate"},
     "Share of confirmed climate advisory deliveries with a recorded farmer response.", None),
    ("IRRIGATION_WATER_VOLUME", "Irrigation water applied (metered)", "Water management", "water", "m3", "monitoring_sum", {},
     "Total metered irrigation water applied on monitored plots in the period.",
     "Define area denominator and comparable baseline method before claiming water-use change."),
    ("SOIL_MOISTURE_MEAN", "Mean soil moisture (sensor)", "Soil & microclimate", "soil", "%", "monitoring_mean", {},
     "Mean of valid soil-moisture monitoring values.", "Sensor coverage is selective; not representative of all plots."),
    ("HH_ADOPTION_RATE_EXAMPLE", "Share of enrolled farmers' households with observed adoption (formula example)",
     "Climate-resilient adoption", "mrv", "%", "configurable_formula",
     {"components": {"adopting": {"method": "households_adopting"}, "enrolled": {"method": "enrolled_farmers"}},
      "expression": "adopting / enrolled * 100"},
     "DEMO configuration example of a configurable formula – not an approved programme indicator.",
     "Illustrates formula configuration only; numerator and denominator units differ (households vs farmers)."),
    ("COV_FARMERS_ENROLLED", "Farmers enrolled with consent", "Programme coverage", None, "farmers", "enrolled_farmers", {},
     "Distinct farmers with an active enrolment and recorded consent at period end.", "Avoid duplicate counts."),
]

DEMO_USERS = [
    ("admin@demo.local", "Demo Platform Admin", "super_admin", None),
    ("programme.admin@demo.local", "Demo Programme Admin", "programme_admin", None),
    ("coordinator.ap@demo.local", "Demo Andhra Pradesh Coordinator", "state_coordinator", "state:AP"),
    ("fpo.admin@demo.local", "Demo Grower Group Coordinator", "fpo_admin", "fpo:0"),
    ("field.officer@demo.local", "Demo Field Officer / CRP (Alluri Sitharama Raju)", "field_officer", "district:AP-0"),
    ("mrv.analyst@demo.local", "Demo MRV Analyst", "mrv_analyst", None),
    ("internal.reviewer@demo.local", "Demo Internal Reviewer", "internal_reviewer", None),
    ("verifier@demo.local", "Demo Independent Verifier", "verifier", None),
    ("farmer@demo.local", "Demo Farmer User", "farmer", None),
    ("researcher@demo.local", "Demo Research Partner", "research_partner", None),
]

# 1x1 PNG and a minimal PDF used as non-sensitive placeholder evidence files.
PLACEHOLDER_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63f8cfc0f01f0005000201"
    "5c0e8a6b0000000049454e44ae426082")
PLACEHOLDER_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
                   b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def _dt(d: date, hour: int = 9) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=IST)


class DemoBuilder:
    def __init__(self, db: Session, password: str, rng_seed: int = 2026) -> None:
        self.db = db
        self.password = password
        self.rng = random.Random(rng_seed)

    def add(self, obj, actor: CurrentUser | None = None):
        if actor is not None and hasattr(obj, "created_by_id"):
            obj.created_by_id = obj.updated_by_id = actor.id
        self.db.add(obj)
        return obj

    # -- programme, geography, organisations, users ----------------------------------------------------------------
    def programme(self) -> None:
        db = self.db
        if db.scalar(select(Programme).where(Programme.code == PROGRAMME_CODE)):
            raise SystemExit(f"Demo data already present (programme {PROGRAMME_CODE} exists). Reset the database first.")
        self.prog = prog = Programme(
            code=PROGRAMME_CODE, name=PROPOSAL_TITLE, version_label="Proposal 29 Oct 2026", proposal_title=PROPOSAL_TITLE,
            proposal_date=date(2026, 10, 29), duration_months=36, status="submitted",
            description="Three-year climate-adaptation and inclusive-livelihoods programme for coffee landscapes in six "
                        "states, FarmFuture-enabled. Adaptation MRV – not a carbon-credit programme.",
            funding_framework="NABARD Climate Change Fund – Interest Differential (CCF-ID)",
            funding_note="Indicative outlay ₹15 crore – proposal only; not sanctioned unless formal approval is provided.",
            is_demo=False)
        db.add(prog)
        db.flush()
        for code, name, value, unit, indicator_code in TARGETS:
            db.add(ProgrammeTarget(programme_id=prog.id, metric_code=code, metric_name=name, target_value=Decimal(value),
                                   unit=unit, period_label="36 months", status="proposed", target_version=1,
                                   indicator_code=indicator_code, source_note=SOURCE_NOTE, is_demo=False))
        self.periods = {}
        for code, name, start, end in [("2026-Q3", "Jul–Sep 2026 (demo)", date(2026, 7, 1), date(2026, 9, 30)),
                                       ("2026-Q4", "Oct–Dec 2026 (demo)", date(2026, 10, 1), date(2026, 12, 31))]:
            self.periods[code] = ReportingPeriod(programme_id=prog.id, code=code, name=name, period_type="quarter",
                                                 start_date=start, end_date=end, is_demo=True)
            db.add(self.periods[code])

        self.states, self.districts, self.clusters = {}, {}, []
        for code, name in STATES:
            self.states[code] = GeoUnit(unit_type="state", name=name, code=f"IN-{code}", is_demo=False)
            if code == "ML":
                self.states[code].requires_confirmation = True
                self.states[code].confirmation_note = ("Provisional: V2 specification text lists Meghalaya; the V2 architecture "
                                                       "diagram lists Odisha instead. Pending programme-owner confirmation.")
            db.add(self.states[code])
        db.flush()
        for sc, names in DISTRICTS.items():
            for i, dname in enumerate(names):
                d = GeoUnit(parent_id=self.states[sc].id, unit_type="district", name=dname, code=f"IN-{sc}-D{i + 1}",
                            is_demo=True)
                db.add(d)
                db.flush()
                self.districts[f"{sc}-{i}"] = d
                for j in range(1 if sc in NORTH_EAST else 2):
                    c = GeoUnit(parent_id=d.id, unit_type="cluster", name=f"{dname} Demo Cluster {chr(65 + j)}",
                                code=f"IN-{sc}-D{i + 1}-C{j + 1}", is_demo=True)
                    db.add(c)
                    self.clusters.append((sc, c))
        db.flush()
        self.fpos = []
        for sc, cluster in self.clusters:
            n = len(self.fpos) + 1
            kind = "FPO" if sc == "AP" else "Grower Group"
            fpo = Organisation(org_type="fpo", code=f"DEMO-GRP-{n:02d}", name=f"Demo Coffee {kind} {n:02d} ({cluster.name})",
                               registration_ref=f"DEMO-REG-{n:04d}", geo_unit_id=cluster.id, contact_name="Demo contact",
                               status="active", is_demo=True)
            db.add(fpo)
            self.fpos.append(fpo)
        partner = Organisation(org_type="implementing_agency", code="DEMO-IMPLEMENTING-AGENCY",
                               name="Implementing agency (demo record)", status="active", is_demo=True)
        db.add(partner)
        db.flush()
        for fpo in self.fpos:
            db.add(ProgrammeOrganisation(programme_id=prog.id, organisation_id=fpo.id, role="implementing_fpo",
                                         geo_unit_id=fpo.geo_unit_id))
        db.add(ProgrammeOrganisation(programme_id=prog.id, organisation_id=partner.id, role="implementing_agency"))

        roles = {r.code: r for r in db.scalars(select(Role))}
        self.users: dict[str, User] = {}
        for email, name, role, scope in DEMO_USERS:
            user = User(email=email, full_name=name, password_hash=hash_password(self.password), roles=[roles[role]],
                        is_demo=True, must_change_password=False)
            if scope:
                kind, ref = scope.split(":")
                if kind == "state":
                    user.scopes = [UserScope(geo_unit_id=self.states[ref].id)]
                elif kind == "district":
                    user.scopes = [UserScope(geo_unit_id=self.districts[ref].id)]
                elif kind == "fpo":
                    user.scopes = [UserScope(organisation_id=self.fpos[int(ref)].id)]
                    user.organisation_id = self.fpos[int(ref)].id
            db.add(user)
            self.users[role] = user
        db.flush()
        self.actors = {code: build_current_user(db, u) for code, u in self.users.items()}

    # -- households, farmers, farms, plots -----------------------------------------------------------------------------
    def registry(self) -> None:
        db, field = self.db, self.actors["field_officer"]
        self.categories = {c.code: c for c in db.scalars(select(BeneficiaryCategory))}
        if db.scalar(select(DemographicField).where(DemographicField.code == "community_name")) is None:
            db.add(DemographicField(code="community_name", label="Community", field_type="text", is_sensitive=True,
                                    requires_consent=True, status="proposed", sort_order=20,
                                    help_text="Sensitive – record only with consent and an approved purpose"))
        self.farmers: list[Farmer] = []
        self.plots: list[FarmPlot] = []
        household = 0
        for f_idx, fpo in enumerate(self.fpos):
            state = next(sc for sc, c in self.clusters if c.id == fpo.geo_unit_id)
            for k in range(10):
                n = f_idx * 10 + k + 1
                if k % 4 != 1:  # roughly one in four households has two registered members
                    household += 1
                consent = "given" if self.rng.random() < 0.9 else "not_recorded"
                woman = self.rng.random() < 0.42
                farmer = self.add(Farmer(
                    farmer_code=f"DEMO-F{n:04d}", household_code=f"DEMO-HH-{household:04d}",
                    full_name=f"Synthetic Farmer {n:03d}", gender="female" if woman else "male",
                    year_of_birth=self.rng.randint(1958, 2000), phone=f"+91-00000-{n:05d}",
                    village=f"Demo Village {chr(65 + k % 5)}", geo_unit_id=fpo.geo_unit_id, primary_fpo_id=fpo.id,
                    consent_status=consent, consent_date=date(2026, 6, 1) + timedelta(days=n % 60) if consent == "given" else None,
                    consent_purpose="Programme participation, advisories and MRV (demo)" if consent == "given" else None,
                    demographics={"community_name": "Demo community"} if state in NORTH_EAST and consent == "given" else None,
                    is_demo=True), field)
                db.flush()
                self.farmers.append(farmer)
                # Explicit DEMO category assignments (synthetic) – the platform never infers categories.
                tribal = self.rng.random() < (0.85 if state in NORTH_EAST else 0.6)
                for category, member in (("WOMAN_BENEFICIARY", woman), ("TRIBAL_COMMUNITY", tribal)):
                    if member and consent == "given":
                        db.add(FarmerCategoryAssignment(farmer_id=farmer.id, category_id=self.categories[category].id,
                                                        status="active", basis="self_declared", assigned_on=date(2026, 7, 1),
                                                        note="DEMO synthetic assignment", is_demo=True))
                if consent == "given" and self.rng.random() < 0.93:
                    db.add(ProgrammeParticipation(programme_id=self.prog.id, farmer_id=farmer.id,
                                                  enrolled_on=date(2026, 7, 1) + timedelta(days=n % 60), status="enrolled",
                                                  is_demo=True))
                farm = self.add(Farm(farmer_id=farmer.id, farm_code=f"DEMO-FARM-{n:04d}", name=f"Demo Estate {n:03d}",
                                     village=farmer.village, geo_unit_id=farmer.geo_unit_id,
                                     total_area=Decimal(str(round(self.rng.uniform(0.3, 1.8), 2))), area_unit="ha",
                                     area_source="declared", is_demo=True), field)
                db.flush()
                for p in range(1 if self.rng.random() < 0.7 else 2):
                    plot = self.add(FarmPlot(
                        farm_id=farm.id, plot_code=f"DEMO-P{n:04d}-{p + 1}",
                        area=Decimal(str(round(self.rng.uniform(0.2, 1.2), 2))) if self.rng.random() > 0.05 else None,
                        area_unit="ha" if self.rng.random() > 0.15 else "acre", area_source="declared",
                        latitude=Decimal(str(round(18.0 + self.rng.random(), 5))),
                        longitude=Decimal(str(round(82.5 + self.rng.random(), 5))),
                        crop=self.rng.choice(["arabica", "robusta", "mixed"]), plantation_year=self.rng.randint(1990, 2022),
                        shade_type=self.rng.choice(["silver oak", "natural forest", "mixed shade"]),
                        irrigation_type=self.rng.choice(["rainfed", "sprinkler", "drip"]), is_demo=True), field)
                    db.flush()
                    self.plots.append(plot)
                # A farm's declared area covers at least its plots
                plotted = sum((area_in_ha(p.area, p.area_unit) for p in self.plots if p.farm_id == farm.id and p.area),
                              Decimal(0))
                farm.total_area = max(farm.total_area, plotted.quantize(Decimal("0.01"), rounding=ROUND_UP))
        ap0 = {c.id for _, c in self.clusters if c.parent_id == self.districts["AP-0"].id}
        self.ap0_farmers = [f for f in self.farmers if f.geo_unit_id in ap0]
        self.users["farmer"].farmer_id = self.ap0_farmers[0].id

    def plots_of(self, farmer: Farmer) -> list[FarmPlot]:
        return [p for p in self.plots if p.farm.farmer_id == farmer.id]

    def field_record(self, rt_code: str, actor: CurrentUser, when: datetime | None, attributes: dict, *, plot=None, geo=None,
                     org=None, device=None, site=None, cluster=None, submit: bool = True, title: str | None = None) -> FieldRecord:
        db = self.db
        rt = record_types.RECORD_TYPES_BY_CODE[rt_code]
        if site is not None and plot is None and site.farm_plot_id:
            plot = db.get(FarmPlot, site.farm_plot_id)
        farmer = db.get(Farmer, plot.farm.farmer_id) if plot else None
        clean, errors = record_types.validate_attributes(rt, attributes)
        value, unit = record_types.value_and_unit(rt, clean) if not errors else (None, None)
        rec = self.add(FieldRecord(
            programme_id=self.prog.id, module_code=rt.module_code, record_type=rt.code, data_nature=rt.data_nature,
            activity_code=rt.default_activity_code, title=title, farmer_id=farmer.id if farmer else None,
            farm_plot_id=plot.id if plot else (device.farm_plot_id if device else None),
            geo_unit_id=(farmer.geo_unit_id if farmer else None) or (device.geo_unit_id if device else None)
            or (site.geo_unit_id if site else None) or geo,
            organisation_id=org or (farmer.primary_fpo_id if farmer else None) or (device.organisation_id if device else None)
            or (site.organisation_id if site else None),
            device_id=device.id if device else None, demonstration_site_id=site.id if site else None,
            surveillance_cluster_id=cluster.id if cluster else None, observed_at=when,
            attributes=clean if not errors else attributes, value=value, unit=unit, is_demo=True), actor)
        db.flush()
        if submit:
            outcome = validate_field_record(db, rec)
            rec.submission_status, rec.validation_status, rec.validation_messages = "submitted", outcome.status, outcome.messages
        return rec

    # -- devices and module records -----------------------------------------------------------------------------------
    def modules(self) -> None:
        db, field, analyst = self.db, self.actors["field_officer"], self.actors["mrv_analyst"]
        self.devices = []
        for i, (dtype, provider) in enumerate([("soilsync_7in1", "SoilSync"), ("soilsync_7in1", "SoilSync"),
                                               ("moisture_probe", "Moisture Probe"), ("moisture_probe", "Moisture Probe"),
                                               ("microclime", "MicroClime"), ("rainsync", "RainSync")]):
            farmer = self.ap0_farmers[i]
            plot = self.plots_of(farmer)[0]
            dev = self.add(Device(device_code=f"DEMO-DEV-{i + 1:03d}", device_type=dtype, provider=provider, farm_plot_id=plot.id,
                                  geo_unit_id=farmer.geo_unit_id, organisation_id=farmer.primary_fpo_id,
                                  installed_on=date(2026, 7, 10), status="active" if i != 3 else "faulty",
                                  deployment_justification="Demo: selective deployment where soil-moisture data gaps exist",
                                  is_demo=True), field)
            db.flush()
            db.add(DeviceCalibration(device_id=dev.id, calibrated_on=date(2026, 7, 10), method="Factory + field check (demo)",
                                     result="pass" if i != 3 else "fail", next_due_on=date(2027, 1, 10),
                                     performed_by="Demo technician"))
            self.devices.append(dev)
        db.flush()
        cluster0 = self.fpos[0].geo_unit_id
        for d in range(10):
            day = date(2026, 9, 20) + timedelta(days=d * 3)
            self.field_record("climate_observation", field, _dt(day, 8), {
                "source_name": "Demo weather station", "station_ref": "DEMO-STN-1",
                "rainfall_mm": round(self.rng.uniform(0, 45) if d % 4 else 0, 1),
                "temp_max_c": round(self.rng.uniform(27, 34), 1), "temp_min_c": round(self.rng.uniform(15, 19), 1),
                "relative_humidity_pct": self.rng.randint(55, 92)}, geo=cluster0)
        self.drought_risk = self.field_record("climate_risk_assessment", analyst, None, {
            "hazard_type": "drought", "risk_level": "high", "valid_from": "2026-10-05", "valid_to": "2026-10-25",
            "model_name": "Demo climate risk model", "model_version": "0.1-demo", "confidence": "Demo only"},
            geo=self.districts["AP-0"].id, title="Drought risk – Alluri Sitharama Raju (demo prediction)")
        self.field_record("climate_risk_assessment", analyst, None, {
            "hazard_type": "excess_rainfall", "risk_level": "moderate", "valid_from": "2026-10-10", "valid_to": "2026-10-20",
            "model_name": "Demo climate risk model"}, geo=self.districts["NL-0"].id, title="Rainfall risk – Kohima (demo prediction)")
        for i in range(8):
            plot = self.plots_of(self.ap0_farmers[i])[0]
            self.field_record("water_measurement", field, _dt(date(2026, 10, 1) + timedelta(days=i), 7), {
                "parameter": "soil_moisture", "value": round(self.rng.uniform(14, 32), 1), "unit": "%", "method": "probe"},
                plot=plot)
            self.field_record("irrigation_event", field, _dt(date(2026, 10, 3) + timedelta(days=i)), {
                "irrigation_method": self.rng.choice(["sprinkler", "drip"]), "duration_hours": round(self.rng.uniform(1, 4), 1),
                "volume": self.rng.randint(20, 90) * 1000, "volume_unit": "litre", "water_source": "well"},
                plot=plot, submit=i != 7)
        for i, practice in enumerate(["mulching", "ground_cover", "contour_bunds", "staggered_trenches", "moisture_retention_pits",
                                      "erosion_control_structure"]):
            attributes = {"practice_type": practice, "area_covered_ha": round(self.rng.uniform(0.1, 0.8), 2),
                          "condition": "good", "implemented_by": "farmer"}
            if practice in ("contour_bunds", "staggered_trenches"):
                attributes["length_m"] = 40
            self.field_record("conservation_activity", field, _dt(date(2026, 10, 8) + timedelta(days=i)), attributes,
                              plot=self.plots_of(self.ap0_farmers[i + 2])[0])
        self.field_record("pest_risk_estimate", analyst, None, {
            "pest_or_disease": "white_stem_borer", "risk_level": "high", "valid_from": "2026-10-01",
            "valid_to": "2026-11-15", "model_name": "Demo pest model"}, geo=self.districts["AP-1"].id,
            title="White stem borer risk (demo prediction)")
        for i in range(6):
            self.field_record("soil_test", field, _dt(date(2026, 8, 12) + timedelta(days=i * 4)), {
                "lab_name": "Demo soil lab", "sample_depth_cm": 30, "ph": round(self.rng.uniform(4.8, 6.4), 1),
                "organic_carbon_pct": round(self.rng.uniform(1.0, 3.0), 2), "nitrogen_kg_ha": self.rng.randint(180, 320),
                "phosphorus_kg_ha": self.rng.randint(10, 35), "potassium_kg_ha": self.rng.randint(120, 300)},
                plot=self.plots_of(self.ap0_farmers[i])[0])
        for i in range(12):
            self.field_record("sensor_observation", field, _dt(date(2026, 10, 1) + timedelta(days=i), 6), {
                "parameter": "soil_moisture", "value": round(self.rng.uniform(15, 34), 1), "unit": "%",
                "quality_flag": "ok" if i != 9 else "suspect"}, device=self.devices[i % 2])
        for sc, cluster in self.clusters:
            if sc in ("AR", "MN"):
                self.field_record("suitability_assessment", analyst, _dt(date(2026, 9, 15)), {
                    "assessment_area": f"{cluster.name} candidate area", "suitability_class": "moderately_suitable",
                    "elevation_m": 1150, "key_constraints": "Slope and winter minimum temperature",
                    "method": "Desk review + site visit (demo)", "assessor": "Demo agronomist", "used_in_planning": False},
                    geo=cluster.id)
                self.field_record("site_finding", field, _dt(date(2026, 9, 16)), {
                    "finding_type": "slope", "description": "Steep slopes on eastern side (demo)",
                    "recommendation": "Contour planting would be required"}, geo=cluster.id)
        for i in range(4):
            plot = self.plots_of(self.ap0_farmers[i + 4])[0]
            self.field_record("plantation_condition", field, _dt(date(2026, 9, 10) + timedelta(days=i)), {
                "overall_condition": ["good", "fair", "fair", "poor"][i], "canopy_cover_pct": [55, 40, 35, 20][i],
                "shade_tree_density_per_ha": [120, 90, 80, 40][i], "method": "Visual transect (demo)"}, plot=plot)
            self.field_record("plantation_survival_check", field, _dt(date(2026, 10, 15) + timedelta(days=i)), {
                "planting_date": "2026-06-20", "plants_planted": 200, "plants_surviving": 200 - self.rng.randint(5, 40),
                "check_round": "3_months"}, plot=plot)
        db.flush()

    # -- community: CRPs, training, demonstration sites, surveillance clusters -----------------------------------------
    def community(self) -> None:
        db, field, admin, fpo_admin = self.db, self.actors["field_officer"], self.actors["super_admin"], self.actors["fpo_admin"]
        statuses = ["trained", "active", "trained", "in_training", "nominated", "active"]
        self.crps = []
        for i in range(12):
            fpo = self.fpos[i % len(self.fpos)]
            status = statuses[i % len(statuses)]
            done = status in ("trained", "active")
            self.crps.append(self.add(CommunityResourcePerson(
                programme_id=self.prog.id, crp_code=f"DEMO-CRP-{i + 1:03d}", full_name=f"Synthetic CRP {i + 1:03d}",
                gender="female" if i % 2 == 0 else "male", phone=f"+91-00001-{i:05d}", organisation_id=fpo.id,
                geo_unit_id=fpo.geo_unit_id, status=status, training_started_on=date(2026, 8, 1),
                training_completed_on=date(2026, 9, 15) if done else None,
                competency_result="competent" if status == "active" else "not_assessed", is_demo=True), admin))
        self.trainings = []
        for i, fpo in enumerate(self.fpos[:4]):
            t = self.field_record("training_session", fpo_admin if i == 0 else admin,
                                  _dt(date(2026, 9, 25) + timedelta(days=i * 5)), {
                                      "topic": "Drought preparedness, mulching and moisture conservation",
                                      "trainer": "Demo extension officer", "mode": "field", "duration_hours": 3},
                                  org=fpo.id, geo=fpo.geo_unit_id, title=f"Training – {fpo.code}")
            for f in [f for f in self.farmers if f.primary_fpo_id == fpo.id][:7]:
                db.add(FieldRecordParticipant(field_record_id=t.id, farmer_id=f.id, attended=self.rng.random() > 0.1))
            self.trainings.append(t)
        self.sites = []
        for i in range(8):
            category = "plantation" if i % 2 == 0 else "soil_moisture"
            host = self.farmers[i * 9]
            site = self.add(DemonstrationSite(
                programme_id=self.prog.id, site_code=f"DEMO-DS-{i + 1:03d}",
                name=f"{'Plantation' if category == 'plantation' else 'Soil & moisture'} demo site {i + 1}",
                category=category, geo_unit_id=host.geo_unit_id, organisation_id=host.primary_fpo_id, host_farmer_id=host.id,
                farm_plot_id=self.plots_of(host)[0].id,
                practices=["SHADE_CANOPY", "PLANTATION_SURVIVAL"] if category == "plantation" else ["MULCHING", "CONTOUR_CONSERVATION"],
                established_on=date(2026, 8, 1) if i < 6 else None, status="active" if i < 6 else "planned",
                selection_note="Demo: illustrative site selection (criteria pending programme approval)", is_demo=True), admin)
            db.flush()
            self.sites.append(site)
            if i < 6:
                for v, kind in enumerate(["setup", "monitoring"]):
                    self.field_record("demonstration_visit", field, _dt(date(2026, 8, 5) + timedelta(days=30 * v + i)), {
                        "visit_type": kind, "practices_observed": ", ".join(site.practices),
                        "participants_count": 12 + i, "women_participants_count": 5 + i % 3,
                        "observations": "Practices established as planned (demo)"}, site=site)
        self.clusters_pest = []
        for i, (_, geo) in enumerate(self.clusters[:5]):
            cluster = self.add(PestSurveillanceCluster(
                programme_id=self.prog.id, cluster_code=f"DEMO-PSC-{i + 1:02d}", name=f"Pest surveillance cluster {i + 1} ({geo.name})",
                geo_unit_id=geo.id, organisation_id=next(f.id for f in self.fpos if f.geo_unit_id == geo.id),
                established_on=date(2026, 8, 15) if i < 4 else None, status="active" if i < 4 else "planned",
                target_pests=["white_stem_borer", "coffee_berry_borer", "leaf_rust"], is_demo=True), admin)
            db.flush()
            self.clusters_pest.append(cluster)
            members = [f for f in self.farmers if f.geo_unit_id == geo.id][:6]
            for m_index, f in enumerate(members):
                db.add(ClusterMembership(cluster_id=cluster.id, farmer_id=f.id, joined_on=date(2026, 8, 20),
                                         role="lead_scout" if m_index == 0 else "participant", is_demo=True))
            if i < 4:
                for k, f in enumerate(members[:3]):
                    inspected = self.rng.randint(40, 80)
                    affected = self.rng.randint(0, 12) if not (i == 0 and k == 2) else inspected + 5  # one rejected example
                    self.field_record("pest_inspection", field, _dt(date(2026, 10, 6) + timedelta(days=k + i)), {
                        "pest_or_disease": self.rng.choice(["white_stem_borer", "leaf_rust"]), "plants_inspected": inspected,
                        "plants_affected": affected, "severity_score": self.rng.randint(0, 3),
                        "scouting_method": "W-pattern walk", "recommended_action": "Trace and remove infested stems (demo)"},
                        plot=self.plots_of(f)[0], cluster=cluster)
        db.flush()

    # -- advisories, deliveries, practices ------------------------------------------------------------------------------
    def operations(self) -> None:
        db, field, admin = self.db, self.actors["field_officer"], self.actors["programme_admin"]
        self.alert = alert = self.add(Advisory(
            programme_id=self.prog.id, origin_module="climate", advisory_type="alert", hazard_type="drought", risk_level="high",
            risk_record_id=self.drought_risk.id, title="Drought alert – conserve soil moisture (demo)",
            message="High drought risk forecast for the next 3 weeks (model estimate).",
            recommendation="Mulch basins, maintain ground cover, prioritise irrigation of young plants.",
            recommended_activity_code="MULCHING", issued_at=_dt(date(2026, 10, 5), 10), valid_until=_dt(date(2026, 10, 25), 18),
            target_geo_unit_id=self.districts["AP-0"].id, status="issued", is_demo=True), admin)
        rain = self.add(Advisory(
            programme_id=self.prog.id, origin_module="climate", advisory_type="advisory", hazard_type="excess_rainfall",
            risk_level="moderate", title="Drainage and erosion-control advisory (demo)", message="Heavy rain expected (model estimate).",
            recommendation="Clear drainage channels and maintain contour bunds.", recommended_activity_code="EROSION_CONTROL",
            issued_at=_dt(date(2026, 10, 9), 9), target_geo_unit_id=self.districts["NL-0"].id, status="issued", is_demo=True), admin)
        self.add(Advisory(programme_id=self.prog.id, origin_module="pest", advisory_type="advisory", hazard_type="pest",
                          risk_level="high", title="White stem borer scouting reminder (draft, demo)",
                          message="Scout for white stem borer in arabica plots.", target_geo_unit_id=self.districts["AP-1"].id,
                          status="draft", is_demo=True), admin)
        db.flush()
        for i, f in enumerate(self.ap0_farmers):
            delivered = i % 6 != 5
            db.add(AdvisoryDelivery(advisory_id=alert.id, farmer_id=f.id, organisation_id=f.primary_fpo_id,
                                    geo_unit_id=f.geo_unit_id, channel="sms", sent_at=_dt(date(2026, 10, 5), 10),
                                    delivered_at=_dt(date(2026, 10, 5), 11) if delivered else None,
                                    response="Will mulch this week" if delivered and i % 3 == 0 else None,
                                    response_at=_dt(date(2026, 10, 6), 9) if delivered and i % 3 == 0 else None,
                                    understood=True if delivered and i % 3 == 0 else None, is_demo=True))
        kohima_clusters = {c.id for _, c in self.clusters if c.parent_id == self.districts["NL-0"].id}
        kohima = [f for f in self.farmers if f.geo_unit_id in kohima_clusters]
        for f in kohima[:8]:
            db.add(AdvisoryDelivery(advisory_id=rain.id, farmer_id=f.id, organisation_id=f.primary_fpo_id, geo_unit_id=f.geo_unit_id,
                                    channel="fpo_meeting", sent_at=_dt(date(2026, 10, 10)), delivered_at=_dt(date(2026, 10, 10)),
                                    is_demo=True))
        codes = ["MULCHING", "GROUND_COVER", "CONTOUR_CONSERVATION", "WATER_MANAGEMENT", "SHADE_CANOPY", "MOISTURE_RETENTION"]
        for i, f in enumerate(self.ap0_farmers[:18]):
            status = ["recommended", "reported", "observed", "observed", "not_adopted", "observed"][i % 6]
            observed = status in ("observed", "not_adopted")
            self.add(PracticeRecord(
                programme_id=self.prog.id, farmer_id=f.id, farm_plot_id=self.plots_of(f)[0].id, geo_unit_id=f.geo_unit_id,
                organisation_id=f.primary_fpo_id, module_code="water", activity_code=codes[i % len(codes)], advisory_id=alert.id,
                recommended_action="Mulch basins and maintain ground cover",
                reported_action="Farmer reports practice started" if status != "recommended" else None,
                observed_action="Practice observed on field visit" if status == "observed" else
                ("Practice not observed on visit" if status == "not_adopted" else None),
                action_date=date(2026, 10, 12) + timedelta(days=i % 10) if status != "recommended" else None,
                evidence_basis="observed" if observed else "reported", status=status,
                observed_by_id=field.id if observed else None, is_demo=True), field)
        for i, f in enumerate(kohima[:5]):
            self.add(PracticeRecord(programme_id=self.prog.id, farmer_id=f.id, geo_unit_id=f.geo_unit_id,
                                    organisation_id=f.primary_fpo_id, module_code="water", activity_code="EROSION_CONTROL",
                                    advisory_id=rain.id, recommended_action="Clear drainage channels",
                                    observed_action="Drainage channels cleared (field visit)" if i < 3 else None,
                                    action_date=date(2026, 10, 18), evidence_basis="observed" if i < 3 else "reported",
                                    status="observed" if i < 3 else "reported", is_demo=True), field)
        for cluster in self.clusters_pest[:2]:
            member = db.scalars(select(ClusterMembership).where(ClusterMembership.cluster_id == cluster.id)
                                .order_by(ClusterMembership.joined_on, ClusterMembership.farmer_id)).first()
            self.add(PracticeRecord(programme_id=self.prog.id, farmer_id=member.farmer_id, geo_unit_id=cluster.geo_unit_id,
                                    organisation_id=cluster.organisation_id, module_code="pest", activity_code="PEST_TREATMENT",
                                    recommended_action="Remove and destroy infested stems",
                                    observed_action="Infested stems removed (follow-up visit)", action_date=date(2026, 10, 20),
                                    evidence_basis="observed", status="observed", is_demo=True), field)
        db.flush()

    # -- indicators, monitoring, baselines --------------------------------------------------------------------------
    def mrv_setup(self) -> None:
        db, admin, field, analyst = self.db, self.actors["super_admin"], self.actors["field_officer"], self.actors["mrv_analyst"]
        self.indicators = {}
        for code, name, family, module, unit, method, params, definition, limitations in INDICATORS:
            rules = {"min": 0, "max": 5000} if code == "IRRIGATION_WATER_VOLUME" else ({"min": 0, "max": 100} if unit == "%" else {})
            self.indicators[code] = self.add(IndicatorDefinition(
                code=code, version=1, name=name, family=family, module_code=module, definition=definition, unit=unit,
                method_code=method, method_parameters=params,
                method_reference=f"Platform aggregation method {method} v1.0 (demo). {PENDING}",
                frequency="quarterly", scope_level="programme/state/district/cluster/FPO", aggregation_rule="See method",
                disaggregation="state, district, FPO/grower group, gender (where approved)", baseline_method=PENDING,
                data_sources="MRV platform records", eligibility=PENDING, exclusions=PENDING,
                verification_sample_approach=PENDING, approval_note=PENDING, validation_rules=rules,
                evidence_requirements=PENDING, limitations=limitations, status="draft", requires_confirmation=True,
                is_demo=True), admin)
        db.flush()
        irr = self.indicators["IRRIGATION_WATER_VOLUME"]
        for i in range(8):
            farmer = self.ap0_farmers[i + 8]
            plot = self.plots_of(farmer)[0]
            litres = Decimal(self.rng.randint(25, 95) * 1000)
            rec = self.add(MonitoringRecord(
                programme_id=self.prog.id, indicator_id=irr.id, activity_code="WATER_MANAGEMENT", farmer_id=farmer.id,
                farm_plot_id=plot.id, geo_unit_id=farmer.geo_unit_id, organisation_id=farmer.primary_fpo_id,
                reporting_period_id=self.periods["2026-Q4"].id, value=litres / 1000, unit="m3", original_value=litres,
                original_unit="litre", observed_at=_dt(date(2026, 10, 2) + timedelta(days=i), 17), method="meter_reading",
                data_nature="observed", submission_status="draft" if i == 7 else "submitted", is_demo=True), field)
            db.flush()
            if i != 7:
                outcome = validate_monitoring(db, rec)
                rec.validation_status, rec.validation_messages = outcome.status, outcome.messages
        src = db.scalars(select(MonitoringRecord).where(MonitoringRecord.indicator_id == irr.id,
                                                        MonitoringRecord.validation_status == "valid")
                         .order_by(MonitoringRecord.observed_at)).first()
        dup = self.add(MonitoringRecord(
            programme_id=self.prog.id, indicator_id=irr.id, farmer_id=src.farmer_id, farm_plot_id=src.farm_plot_id,
            geo_unit_id=src.geo_unit_id, organisation_id=src.organisation_id, value=src.value, unit="m3",
            observed_at=src.observed_at, method="meter_reading", submission_status="submitted", is_demo=True), field)
        db.flush()
        outcome = validate_monitoring(db, dup)
        dup.validation_status, dup.validation_messages = outcome.status, outcome.messages  # → exception (possible duplicate)
        sm = self.indicators["SOIL_MOISTURE_MEAN"]
        for rec in db.scalars(select(FieldRecord).where(FieldRecord.record_type == "sensor_observation",
                                                        FieldRecord.validation_status == "valid")).unique():
            mon = self.add(MonitoringRecord(
                programme_id=self.prog.id, indicator_id=sm.id, farmer_id=rec.farmer_id, farm_plot_id=rec.farm_plot_id,
                geo_unit_id=rec.geo_unit_id, organisation_id=rec.organisation_id, field_record_id=rec.id, value=rec.value,
                unit="%", observed_at=rec.observed_at, method="sensor", submission_status="submitted", is_demo=True), field)
            db.flush()
            outcome = validate_monitoring(db, mon)
            mon.validation_status, mon.validation_messages = outcome.status, outcome.messages
        for code, value, unit, method in [("IRRIGATION_WATER_VOLUME", Decimal("300"), "m3", "Farmer recall survey (demo)"),
                                          ("SOIL_MOISTURE_MEAN", Decimal("21.5"), "%", "Probe sampling (demo)")]:
            b = self.add(BaselineRecord(indicator_id=self.indicators[code].id, programme_id=self.prog.id, scope_type="programme",
                                        value=value, unit=unit, period_label="2025 post-monsoon (demo)",
                                        period_start=date(2025, 10, 1), period_end=date(2025, 12, 31), method=method,
                                        source="Demo baseline survey", data_nature="observed", submission_status="submitted",
                                        is_demo=True), analyst)
            db.flush()
            outcome = validate_baseline(db, b)
            b.validation_status, b.validation_messages = outcome.status, outcome.messages
        db.add(BaselineRecord(indicator_id=self.indicators["HH_CLIMATE_RESILIENT_ADOPTION"].id, programme_id=self.prog.id,
                              scope_type="programme", value=Decimal(0), unit="households", period_label="Programme start",
                              submission_status="draft", is_demo=True, created_by_id=analyst.id,
                              notes="Placeholder – baseline method pending approval"))
        db.flush()

    # -- integrations (mock adapters only) ------------------------------------------------------------------------------
    def integrations(self) -> None:
        db, admin = self.db, self.actors["super_admin"]
        mock = IntegrationSource(source_code="mock_water_meter", name="Mock water-meter system (DEMO)", module_code="water",
                                 source_type="rest_pull", adapter_code="mock_water_meter", auth_type="none",
                                 owner="Demo – no real system", enabled=True, is_mock=True,
                                 availability_note="MOCK adapter generating synthetic data. Not a live integration.",
                                 config={"retries": 3, "max_pages": 10})
        iot = IntegrationSource(source_code="mock_iot_gateway", name="Mock IoT device gateway (DEMO)", module_code="soil",
                                source_type="rest_pull", adapter_code="mock_iot_gateway", auth_type="none",
                                owner="Demo – device protocols pending final equipment schedule", enabled=True, is_mock=True,
                                availability_note="MOCK gateway for SoilSync / Moisture Probe / MicroClime / RainSync readings.",
                                config={"payload_defaults": {"programme_code": PROGRAMME_CODE, "module_code": "soil",
                                                             "record_type": "sensor_observation"}})
        water = IntegrationSource(source_code="water_management", name="FarmFuture Water Balance Engine", module_code="water",
                                  source_type="rest_push", adapter_code="push_only", auth_type="api_key",
                                  owner="FarmFuture (to be confirmed)", enabled=True, is_mock=False,
                                  availability_note="Inbound endpoint ready. API/data contract, units and method version not yet "
                                                    "confirmed by the source owner; no data received from a live system.")
        climate = IntegrationSource(source_code="climate_intelligence", name="FarmFuture Climate Intelligence Engine",
                                    module_code="climate", source_type="rest_pull", adapter_code="unconfigured_rest",
                                    base_url_env_var="CLIMATE_ENGINE_BASE_URL", auth_type="api_key",
                                    owner="FarmFuture (to be confirmed)", enabled=True, is_mock=False,
                                    availability_note="Placeholder. API documentation not supplied – requires confirmation.")
        pest = IntegrationSource(source_code="pest_prediction", name="FarmFuture Pest & Disease Prediction Engine",
                                 module_code="pest", source_type="rest_pull", adapter_code="unconfigured_rest",
                                 base_url_env_var="PEST_ENGINE_BASE_URL", auth_type="api_key",
                                 owner="FarmFuture (to be confirmed)", enabled=True, is_mock=False,
                                 availability_note="Placeholder. Supported risks, model/version and API pending confirmation.")
        db.add_all([mock, iot, water, climate, pest])
        seed_programme_decisions(db, self.prog.id)
        db.flush()
        for source_field, target, value_map in [("id", "source_record_id", None), ("plotRef", "farm_plot_external_id", None),
                                                ("qty", "value", None), ("uom", "unit", {"L": "litre", "KL": "m3"}),
                                                ("readingTime", "observed_at", None), ("kind", "indicator_code", None),
                                                ("prog", "programme_code", None), ("activity", "activity_code", None),
                                                ("how", "method", None)]:
            db.add(IntegrationFieldMapping(source_id=mock.id, payload_kind="measurements", source_field=source_field,
                                           target_field=target, value_map=value_map))
        for source_field, target, value_map in [
            ("msgId", "source_record_id", None), ("deviceId", "device_code", None), ("ts", "observed_at", None),
            ("param", "attributes.parameter", {"VWC": "soil_moisture", "PH": "ph", "EC": "ec", "TAIR": "air_temperature",
                                               "RH": "humidity", "RAIN": "rainfall"}),
            ("val", "attributes.value", None),
            ("unit", "attributes.unit", {"%VWC": "%", "degC": "°C", "%RH": "%"}),
            ("q", "attributes.quality_flag", None)]:
            db.add(IntegrationFieldMapping(source_id=iot.id, payload_kind="observations", source_field=source_field,
                                           target_field=target, value_map=value_map))
        db.flush()
        for source in (mock, iot):
            adapter = get_adapter(db, source)
            records, cursor = [], None
            while True:
                page = with_retry(lambda c=cursor, a=adapter: a.fetch_page(c), base_delay=0.01)
                records.extend(page.records)
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
            process_batch(db, source, adapter.payload_kind, records, "pull", admin)
        process_batch(db, water, "measurements", [
            {"source_system": "water_management", "source_record_id": "WM-DEMO-1", "programme_code": PROGRAMME_CODE,
             "farm_plot_external_id": self.plots[3].plot_code, "activity_code": "WATER_MANAGEMENT",
             "indicator_code": "IRRIGATION_WATER_VOLUME", "value": 80000, "unit": "litre",
             "observed_at": "2026-10-29T10:30:00+05:30", "method": "meter_reading", "evidence": [{"source_evidence_id": "EV-77"}]},
            {"source_record_id": "WM-DEMO-2", "programme_code": PROGRAMME_CODE, "farm_plot_external_id": "P52",
             "indicator_code": "IRRIGATION_WATER_VOLUME", "value": 1200, "unit": "bucket", "observed_at": "2026-10-29T11:00:00"},
        ], "push", admin)

    # -- evidence, independent verification sample, MRV workflow -------------------------------------------------------
    def evidence_and_mrv(self) -> None:
        db = self.db
        field, analyst = self.actors["field_officer"], self.actors["mrv_analyst"]
        reviewer, verifier, admin = self.actors["internal_reviewer"], self.actors["verifier"], self.actors["super_admin"]
        storage = get_storage()
        observed = db.scalars(select(PracticeRecord).where(PracticeRecord.status == "observed")
                              .order_by(PracticeRecord.action_date, PracticeRecord.farmer_id)).first()
        for content, name, mime, kind, entity_type, owner, purpose in [
            (PLACEHOLDER_PNG, "demo-mulching-photo.png", "image/png", "photo", "practice_record", observed, "practice_photo"),
            (PLACEHOLDER_PDF, "demo-attendance-sheet.pdf", "application/pdf", "field_form", "field_record", self.trainings[0],
             "attendance_sheet"),
            (PLACEHOLDER_PNG, "demo-site-photo.png", "image/png", "photo", "demonstration_site", self.sites[0], "site_photo"),
        ]:
            key = storage.save(content, name)
            asset = self.add(EvidenceAsset(storage_key=key, filename=name, mime_type=mime, size_bytes=len(content),
                                           checksum_sha256=checksum(content), description="DEMO placeholder file",
                                           evidence_type=kind, source="field_upload", captured_at=_dt(date(2026, 10, 14)),
                                           access_classification="internal", geo_unit_id=owner.geo_unit_id,
                                           organisation_id=owner.organisation_id, is_demo=True), field)
            db.flush()
            db.add(EvidenceLink(evidence_asset_id=asset.id, entity_type=entity_type, entity_id=owner.id, purpose=purpose,
                                created_by_id=field.id))
        # Independent sample verification of field-observed adoption (seeded, reproducible random draw).
        sample = self.add(VerificationSample(
            programme_id=self.prog.id, reporting_period_id=self.periods["2026-Q4"].id,
            title="Q4 2026 adoption verification (demo)", entity_type="practice_record",
            frame_description="Field-observed practice records, Oct–Dec 2026 (demo frame)",
            frame_filter={"period_start": "2026-10-01", "period_end": "2026-12-31", "practice_status": "observed"},
            selection_method="simple_random", sample_size=6, random_seed=2026, verifier_id=verifier.id, is_demo=True), verifier)
        db.flush()
        items = verification_service.draw(db, verifier, sample, None)
        for i, item in enumerate(items):
            result = {0: "discrepancy", 1: "not_visited"}.get(i, "confirmed")
            verification_service.record_result(db, verifier, item, sample, {
                "result": result, "visit_date": date(2026, 11, 2) if result != "not_visited" else None,
                "observation": "Practice present as recorded (demo)" if result == "confirmed" else None,
                "discrepancy_note": "Mulch cover much smaller than recorded (demo)" if result == "discrepancy" else None})
        db.add(VerificationFinding(verification_sample_id=sample.id, finding_type="data_quality", severity="minor",
                                   description="One observed practice overstated in the field record (demo finding)",
                                   corrective_action="Re-train field team on observation forms", corrective_status="open",
                                   is_demo=True, created_by_id=verifier.id))
        verification_service.complete(db, verifier, sample, "discrepancies_found",
                                      f"{len(items) - 2} of {len(items)} confirmed, 1 discrepancy, 1 not visited (demo).")

        # DEMO-labelled checklist items (optional) so criteria and checklists can be demonstrated; real criteria are
        # configured by the programme owner.
        for key, item in (("demonstration.selection_criteria", {"code": "DEMO_HOST_CONSENT", "required": False,
                                                                "label": "DEMO criterion – host farmer consent recorded"}),
                          ("surveillance.cluster_criteria", {"code": "DEMO_LEAD_SCOUT", "required": False,
                                                             "label": "DEMO criterion – lead scout identified"}),
                          ("verification.visit_checklist", {"code": "DEMO_PRACTICE_SEEN", "required": False,
                                                            "label": "DEMO check – practice seen on the plot"})):
            db.get(SystemSetting, key).value = [item]
        for site in self.sites[:6]:
            site.criteria_assessment = {"DEMO_HOST_CONSENT": {"met": True, "note": "DEMO"}}

        # Demo only: allow PROVISIONAL calculations while indicator definitions are pending approval.
        setting = db.get(SystemSetting, "mrv.allow_provisional_calculations")
        setting.value = True
        setting.description = (setting.description or "") + " Enabled by the DEMO seed – disable for real programme data."
        q3, q4 = self.periods["2026-Q3"], self.periods["2026-Q4"]
        _, enrolled = mrv.run_calculation(db, analyst, self.indicators["COV_FARMERS_ENROLLED"], self.prog.id, q4.start_date,
                                          q4.end_date, q4.id, "programme", None, None)
        task = mrv.submit_for_review(db, analyst, "indicator_result", enrolled.id, reviewer.id, date(2026, 11, 15),
                                     "Enrolment count for Q4 (demo)")
        mrv.mark_reviewed(db, reviewer, task, "Spot-checked enrolment records against consent dates (demo)")
        mrv.decide(db, verifier, task, "verified", "Enrolment and consent records reconcile with the registry (demo).")

        irr = self.indicators["IRRIGATION_WATER_VOLUME"]
        _, irr_result = mrv.run_calculation(db, analyst, irr, self.prog.id, q4.start_date, q4.end_date, q4.id, "programme", None, None)
        task2 = mrv.submit_for_review(db, analyst, "indicator_result", irr_result.id, reviewer.id, date(2026, 11, 20),
                                      "Metered irrigation volume Q4 (demo)")
        db.add(VerificationFinding(review_task_id=task2.id, finding_type="evidence_gap", severity="major",
                                   description="Meter photos missing for 3 records (demo finding)",
                                   corrective_action="Upload meter photos or exclude records", corrective_status="open",
                                   is_demo=True, created_by_id=reviewer.id))
        mrv.request_correction(db, reviewer, task2, "Provide meter photo evidence for flagged records (demo)")

        for code in ("HH_CLIMATE_RESILIENT_ADOPTION", "HH_SOIL_MOISTURE_CONSERVATION", "HH_PEST_SURVEILLANCE",
                     "WOMEN_BENEFICIARIES", "WOMEN_ADOPTING", "CRPS_TRAINED", "CRPS_WOMEN_TRAINED", "DEMO_SITES_PLANTATION",
                     "DEMO_SITES_SOIL_MOISTURE", "PEST_SURVEILLANCE_CLUSTERS_ACTIVE", "CLIM_FARMERS_REACHED_ALERTS"):
            _, res = mrv.run_calculation(db, analyst, self.indicators[code], self.prog.id, q3.start_date, q4.end_date, None,
                                         "programme", None, None)
            if code == "HH_CLIMATE_RESILIENT_ADOPTION":
                mrv.submit_for_review(db, analyst, "indicator_result", res.id, reviewer.id, date(2026, 11, 25),
                                      "Household adoption to date (demo, provisional)")
        mrv.run_calculation(db, admin, self.indicators["COV_FARMERS_ENROLLED"], self.prog.id, q4.start_date, q4.end_date, q4.id,
                            "district", self.districts["AP-0"].id, None)
        db.flush()

    def run(self) -> dict:
        self.programme()
        self.registry()
        self.modules()
        self.community()
        self.operations()
        self.mrv_setup()
        self.integrations()
        self.evidence_and_mrv()
        return {"programme_id": self.prog.id, "users": [u[0] for u in DEMO_USERS], "farmers": len(self.farmers),
                "plots": len(self.plots)}


def seed_demo(db: Session, password: str) -> dict:
    return DemoBuilder(db, password).run()
