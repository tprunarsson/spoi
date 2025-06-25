import requests
import urllib3
import json
from spoi.db.models import Course, CourseOffering
from spoi.db.session import SessionLocal

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BATCH_SIZE = 50

def fetch_all_course_ids(year=2025):
    url = f"https://localhost:8443/service/hi/?request=namskeid_kennsluars&kennsluar={year}"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return [entry['ke_fagnumer'] for entry in response.json().get('data', [])]

def fetch_courses_batch(course_ids):
    ids_param = ",".join(course_ids)
    url = f"https://localhost:8443/service/hi/?request=namskeid&id={ids_param}"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json().get('data', {})

def extract_course_code_from_offering_id(course_offering_id):
    import re
    m = re.match(r"(.+?)(\d{5,6})$", course_offering_id)
    return m.group(1) if m else course_offering_id

def parse_course_offering_id(course_offering_id):
    year = course_offering_id[-5:-1]
    term = course_offering_id[-1]
    base_id = course_offering_id[:-5]
    return {"year": year, "term": term, "base_id": base_id}

def import_courses_and_offerings(year=2025):
    session = SessionLocal()
    all_course_ids = fetch_all_course_ids(year)
    total_courses = len(all_course_ids)
    print(f"[INFO] Fetching and importing {total_courses} courses for year {year}...")

    for i in range(0, total_courses, BATCH_SIZE):
        batch_ids = all_course_ids[i:i + BATCH_SIZE]
        print(f"[INFO] Processing courses {i+1} to {i+len(batch_ids)}")
        batch_data = fetch_courses_batch(batch_ids)

        for courseOfferingId in batch_ids:
            course_record = batch_data.get(courseOfferingId)
            if not course_record:
                print(f"[WARNING] No data returned for {courseOfferingId}")
                continue

            data = course_record.get("data", {})
            skor = course_record.get("skor", {})
            departmentId = skor.get("deildId") if isinstance(skor, dict) else None

            courseCode = data.get("ke_stuttfagnumer") or extract_course_code_from_offering_id(courseOfferingId)
            courseCode = courseCode.strip()

            canonicalName_en = data.get('ke_e_fagheiti', '')
            canonicalName_is = data.get('ke_fagheiti', '')

            # --- Catalog-level Course ---
            course = Course(courseCode=courseCode)
            course.set_canonical_name(canonicalName_en, canonicalName_is)
            session.merge(course)

            # --- Offering-level ---
            offering_id_info = parse_course_offering_id(courseOfferingId)
            name = {
                "en": data.get('ke_e_fagheiti', ''),
                "is": data.get('ke_fagheiti', '')
            }
            description = {
                "en": data.get('ke_e_namskeidslys', ''),
                "is": data.get('ke_namskeidslys', '')
            }
            try:
                ects = float(data.get('ke_einingar', 0))
            except (TypeError, ValueError):
                ects = None

            timeslot_fields = [
                ('fl', 'lecture'),
                ('du', 'tutorial'),
                ('ae', 'exercise'),
                ('vl', 'lab'),
                ('ut', 'discussion'),
                ('hp', 'project'),
                ('ek', 'unknown'),
            ]
            component_data = []
            for short, type_name in timeslot_fields:
                hours = data.get(f'ke_fjoldi_{short}_tima', 0)
                weeks = data.get(f'ke_fjoldi_{short}_vikur', 0)
                if hours or weeks:
                    component_data.append({
                        "type": type_name,
                        "hours": int(hours) if hours else 0,
                        "weeks": int(weeks) if weeks else 0,
                    })

            longCourseCode = data.get("ke_langtfagnumer")
            languageOfInstruction = data.get("kennslutungumal")
            namsmat = json.dumps({
                "is": data.get("ke_namsmat", ""),
                "en": data.get("ke_e_namsmat", "")
            })
            textbooks = json.dumps({
                "is": data.get("ke_namsbaekur", ""),
                "en": data.get("ke_e_namsbaekur", "")
            })
            misc = json.dumps({
                "ke_stokkur": data.get("ke_stokkur"),
                "ke_kennt_a_ensku": data.get("ke_kennt_a_ensku"),
                "ke_lokaprof_tegund": data.get("ke_lokaprof_tegund"),
                "ke_tegund": data.get("ke_tegund"),
                "ke_namsstig": data.get("ke_namsstig"),
                "er_fjarnam": data.get("er_fjarnam"),
                "er_vettvangsnam": data.get("er_vettvangsnam"),
            })

            namsfyrirkomulag = course_record.get("namsfyrirkomulag", {})
            if namsfyrirkomulag.get("stadnam") == "t":
                modeOfDelivery = "onsite"
            elif namsfyrirkomulag.get("fjarnam") == "t":
                modeOfDelivery = "remote"
            elif namsfyrirkomulag.get("stadlotur") == "t":
                modeOfDelivery = "block"
            else:
                modeOfDelivery = "unknown"

            isCoTaught = namsfyrirkomulag.get("samkennt") == "t"
            hasFinalExam = data.get("er_birt_i_proftoflu") == "t"

            stillingar = course_record.get("stillingar", {})
            if not stillingar:
                stillingar = {}
            elif isinstance(stillingar, list):
                stillingar = stillingar[0] if stillingar else {}

            learningOutcomes = json.dumps({
                "is": stillingar.get("haefnisvidmid_is", ""),
                "en": stillingar.get("haefnisvidmid_en", "")
            })
            assessment = json.dumps({
                "is": data.get("ke_namsmat", ""),
                "en": data.get("ke_e_namsmat", "")
            })
            learningMaterials = json.dumps({
                "is": data.get("ke_namsbaekur", ""),
                "en": data.get("ke_e_namsbaekur", "")
            })
            prerequisites = json.dumps({
                "is": data.get("ke_undirstada", ""),
                "en": data.get("ke_e_undirstada", "")
            })

            # --- Construct and merge CourseOffering ---
            course_offering = CourseOffering(
                courseOfferingId=str(data.get('ke_fagnumer')),
                courseCode=courseCode,
                academicYear=offering_id_info['year'],
                term=offering_id_info['term'],
                name=json.dumps(name),
                description=json.dumps(description),
                ects=ects,
                departmentId=departmentId,
                timeslotPattern=json.dumps(component_data) if component_data else None,
                longCourseCode=longCourseCode,
                languageOfInstruction=languageOfInstruction,
                namsmat=namsmat,
                misc=misc,
                learningOutcomes=learningOutcomes,
                assessment=assessment,
                learningMaterials=learningMaterials,
                prerequisites=prerequisites,
                modeOfDelivery=modeOfDelivery,
                isCoTaught=isCoTaught,
                hasFinalExam=hasFinalExam
            )
            session.merge(course_offering)

        session.commit()

    session.close()
    print("\n✅ Courses and offerings imported successfully.")

if __name__ == "__main__":
    import_courses_and_offerings(year=2025)
