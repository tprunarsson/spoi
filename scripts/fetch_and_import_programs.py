import requests
import urllib3
import json
from spoi.db.models import Program, ProgramOffering, FieldOfStudy, CurriculumComponent
from spoi.db.session import SessionLocal

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def fetch_program_catalog(year, lang="is"):
    url = f"https://localhost:8443/service/hi/?request=namsleidir_kennsluars&kennsluar={year}&lang={lang}"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json().get("data", [])

def fetch_program_offering_detail(program_offering_id, utgafa, lang="is"):
    url = f"https://localhost:8443/service/hi/?request=namsleid&id={program_offering_id}&utgafa={utgafa}&lang={lang}"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json().get("data", {})

def fetch_all_programs(year):
    url = f"https://localhost:8443/service/toflugerd/?request=pidinfo&year={year}"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json().get("data", {}).get(str(year), {})

def fetch_program_courses(year, scid, did, pid, season):
    if season == "fall":
        year_ = year - 1
    else:
        year_ = year
    url = (
        "https://localhost:8443/service/toflugerd/"
        "?request=cidinfodependTEST"
        f"&year={year_}&scid={scid}&did={did}&pid={pid}&season={season}"
    )
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    data = response.json().get("data", {})
    if not isinstance(data, dict):
        return {}
    return data.get(str(year_), {}).get(str(scid), {}).get(str(did), {}).get(pid, {})

def safe_json_field(data, key, subkey):
    try:
        return data.get(key, {}).get(subkey, "")
    except Exception:
        return ""

def import_all_academic_structures(year=2025):
    session = SessionLocal()

    # --- STEP 1: Import Programs (and basic fields) ---
    catalog_is = fetch_program_catalog(year, lang="is")
    catalog_en = fetch_program_catalog(year, lang="en")
    en_by_id = {entry['id']: entry for entry in catalog_en}

    for entry in catalog_is:
        programId = entry["stuttnumer"]
        entry_id = entry["id"]
        entry_en = en_by_id.get(entry_id, {})
        names = json.dumps({
            "is": entry.get("heiti", ""),
            "en": entry_en.get("heiti", "")
        })
        langtnumer = entry.get("langtnumer", "")
        institutionId = langtnumer[:2] if len(langtnumer) >= 4 else None
        departmentId = langtnumer[2:4] if len(langtnumer) >= 4 else None

        # Defensive upsert
        program = session.query(Program).filter_by(programId=programId).first()
        if not program:
            program = Program(programId=programId)
            session.add(program)
        program.name = names
        program.departmentId = departmentId
        program.institutionId = institutionId
        program.levelShort = None
        program.level = entry.get("namsstig")
        program.diplomaType = entry.get("grada")

    session.commit()
    print(f"Imported canonical Programs for year {year}")

    # --- STEP 2: ProgramOfferings ---
    for entry in catalog_is:
        programOfferingId = entry["id"]
        utgafa = entry.get("utgafa")
        programId = entry["stuttnumer"]
        details_is = fetch_program_offering_detail(programOfferingId, utgafa, lang="is")
        details_en = fetch_program_offering_detail(programOfferingId, utgafa, lang="en")
        raw_is = details_is.get("raw", {})
        raw_en = details_en.get("raw", {})
        title = json.dumps({
            "is": details_is.get("heiti", ""),
            "en": details_en.get("heiti", "")
        })
        degree = json.dumps({
            "is": entry.get("grada", ""),
            "en": details_en.get("gerd_en", "")
        })
        ects = entry.get("einingar", "")
        learningOutcomes = json.dumps({
            "is": raw_is.get("menntunarmarkmid", ""),
            "en": raw_en.get("menntunarmarkmid_en", "")
        })
        description = json.dumps({
            "is": safe_json_field(details_is, "marktextar", "efsti_texti"),
            "en": safe_json_field(details_en, "marktextar", "efsti_texti")
        })
        about = json.dumps({
            "is": safe_json_field(details_is, "marktextar", "um_hvad"),
            "en": safe_json_field(details_en, "marktextar", "um_hvad")
        })
        admission = json.dumps({
            "is": raw_is.get("ds3_3is", ""),
            "en": raw_en.get("ds3_3en", "")
        })
        web_url = safe_json_field(details_is, "stillingar", "ytrivefs_slod_is") or raw_is.get("ds6_2is", "")

        offering = session.query(ProgramOffering).filter_by(programOfferingId=programOfferingId).first()
        if not offering:
            offering = ProgramOffering(programOfferingId=programOfferingId, programId=programId)
            session.add(offering)
        offering.academicYear = str(year)
        offering.utgafa = utgafa
        offering.stuttnumer = programId
        offering.langtnumer = entry.get("langtnumer")
        offering.title = title
        offering.degree = degree
        offering.ects = ects
        offering.namsstig = entry.get("namsstig")
        offering.learningOutcomes = learningOutcomes
        offering.description = description
        offering.about = about
        offering.admission = admission
        offering.web_url = web_url
        offering.active = entry.get("birta_i_kennsluskra", "1") == "1"

    session.commit()
    print(f"Imported program offerings and details for year {year}.")

    # --- STEP 3: Fields of Study & Curriculum (from toflugerd API) ---
    print("Importing fields of study and curriculum structure...")
    program_data = fetch_all_programs(year)
    seasons = ["fall", "spring", "summer"]

    for institutionId, departments in program_data.items():
        for departmentId, programs in departments.items():
            for programCode, pdata in programs.items():
                programId = programCode
                shortCode = programCode
                longCode = None
                name_is = pdata.get("nameice", "")
                name_en = pdata.get("nameeng", "")
                levelShort = pdata.get("levelshort", "")
                level = pdata.get("level", "")
                diplomaType = pdata.get("gradename", "")
                curriculumYear = str(year)
                programType = None
                isCrossDisciplinary = None

                # Upsert Program (again, to fill gaps)
                program = session.query(Program).filter_by(programId=programId).first()
                if not program:
                    program = Program(programId=programId)
                    session.add(program)
                program.name = json.dumps({"is": name_is, "en": name_en})
                program.shortCode = shortCode
                program.longCode = longCode
                program.departmentId = departmentId
                program.institutionId = institutionId
                program.levelShort = levelShort
                program.level = level
                program.diplomaType = diplomaType
                program.curriculumYear = curriculumYear
                program.programType = programType
                program.isCrossDisciplinary = isCrossDisciplinary

                # --- Fields of Study ---
                raw_fids = pdata.get("fid")
                if not raw_fids or raw_fids == []:
                    field_ids = [f"{programCode}-0"]
                    raw_fids = ["-1"]
                else:
                    field_ids = [
                        f"{programCode}-0" if str(fid) == "-1" else f"{programCode}-{fid}"
                        for fid in raw_fids
                    ]

                fid_map = dict()
                for fid, canonical_fid in zip(raw_fids, field_ids):
                    fid_map[str(fid)] = canonical_fid

                for fieldOfStudyId in set(field_ids):
                    field = session.query(FieldOfStudy).filter_by(
                        fieldOfStudyId=str(fieldOfStudyId),
                        shortName=shortCode,
                        programId=programId
                    ).first()
                    if not field:
                        field = FieldOfStudy(
                            fieldOfStudyId=str(fieldOfStudyId),
                            shortName=shortCode,
                            programId=programId
                        )
                        session.add(field)
                    field.set_names(name_en, name_is)
                    field.longName = None

                # --- Curriculum ---
                for season in seasons:
                    field_courses = fetch_program_courses(year, institutionId, departmentId, programCode, season)
                    for raw_fid, courses in field_courses.items():
                        canonical_fid = fid_map.get(str(raw_fid))
                        if not canonical_fid:
                            if str(raw_fid) == "-1":
                                canonical_fid = f"{programCode}-0"
                            else:
                                canonical_fid = f"{programCode}-{raw_fid}"
                        for courseId, info in courses.items():
                            cc = session.query(CurriculumComponent).filter_by(
                                fieldOfStudyId=canonical_fid,
                                courseId=courseId
                            ).first()
                            if not cc:
                                cc = CurriculumComponent(
                                    fieldOfStudyId=canonical_fid,
                                    courseId=courseId
                                )
                                session.add(cc)
                            cc.requirementType = info.get("requirement")
                            cc.studyYear = info.get("year")
                            cc.semester = info.get("season")
                            # Optionally set more fields from info

    session.commit()
    session.close()
    print(f"All academic structures imported for year {year}.")

if __name__ == "__main__":
    import_all_academic_structures(year=2025)
