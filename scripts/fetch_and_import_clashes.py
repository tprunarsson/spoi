import requests
import urllib3
from itertools import combinations
from collections import Counter, defaultdict
from datetime import datetime
from datetime import datetime, UTC

from spoi.db.models import CourseClashCount, CourseStudentCount, CourseOffering
from spoi.db.session import SessionLocal

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def fetch_student_field_registrations(year=2025):
    url = f"https://localhost:8443/service/toflugerd/?request=sidbyfid&year={year}"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json()

def fetch_student_course_registrations_old(year=2025):
    url = f"https://localhost:8443/service/toflugerd/?request=sidinfo&year={year}&season=fall"
    response = requests.get(url, verify=False)
    response.encoding = 'utf-8'
    return response.json()

def fetch_student_course_registrations(year=2025):
    # Fall
    url_fall = f"https://localhost:8443/service/toflugerd/?request=sidinfo&year={year}&season=fall"
    resp_fall = requests.get(url_fall, verify=False)
    resp_fall.encoding = 'utf-8'
    total_response = resp_fall.json()

    # Spring
    url_spring = f"https://localhost:8443/service/toflugerd/?request=sidinfo&year={year+1}&season=spring"
    resp_spring = requests.get(url_spring, verify=False)
    resp_spring.encoding = 'utf-8'
    spring_json = resp_spring.json()

    # Merge spring into fall
    # Both total_response and spring_json have a 'data' key which is a dict
    # Insert spring's year as a string!
    spring_year_str = str(year+1)
    if 'data' in spring_json and spring_year_str in spring_json['data']:
        total_response['data'][spring_year_str] = spring_json['data'][spring_year_str]
    else:
        # Defensive: make sure we add even if empty
        total_response['data'][spring_year_str] = {}

    return total_response


def import_course_clash_and_student_counts_with_fields(year=2025):
    session = SessionLocal()
    academic_year = str(year)
    fetch_time = datetime.now(UTC)
    
    # --- Step 1: Build mapping of long names to courseOfferingIds ---
    course_id_map = {}
    academic_years = [str(year), str(year+1)]
    offerings = session.query(CourseOffering).filter(CourseOffering.academicYear.in_(academic_years)
).all()
    for offering in offerings:
        try:
            long_name = offering.longCourseCode
            course_id_map[long_name] = offering.courseOfferingId
        except Exception as e:
            print(f"[WARN] Skipping offering {offering.courseOfferingId} due to error: {e}")

    # --- Step 2: Load field-of-study registrations and course registrations ---
    field_registrations = fetch_student_field_registrations(year)
    course_registrations = fetch_student_course_registrations(year)
    #print(course_registrations)
    
    # Build a mapping from student_id -> list of (programId, fieldOfStudyId)
    student_program_field = defaultdict(list)
    for year_val, insts in field_registrations.get("data", {}).items():
        for inst_id, depts in insts.items():
            for dept_id, programs in depts.items():
                for program_id, fields in programs.items():
                    for field_of_study_id, student_list in fields.items():
                        for student_id in student_list:
                            student_program_field[student_id].append((program_id, field_of_study_id))

    # --- Step 3: Build course enrollments by student ---
    # student_courses: student_id -> list of registered courseOfferingIds
    student_courses = defaultdict(list)
    year_keys = [str(year), str(year+1)]
    for y in year_keys:
        year_data = course_registrations.get('data', {}).get(str(y), {})
        for faculty_id, students_by_faculty in year_data.items():
            for student_id, study_tracks in students_by_faculty.items():
                for ferill_str, reg_info in study_tracks.items():
                    original_courses = reg_info.get('lcid', [])
                    # Map long course names to offering IDs
                    mapped_courses = []
                    for course in original_courses:
                        course_id = course_id_map.get(course)
                        if course_id is not None:
                            mapped_courses.append(course_id)
                    if mapped_courses:
                        student_courses[student_id].extend(mapped_courses)

    # --- Step 4: Calculate clashes and counts, keyed by fieldOfStudyId ---
    clash_counter = Counter()
    student_sets = defaultdict(set)

    for student_id, course_list in student_courses.items():
        # For each (program, fieldOfStudyId) for this student
        for program_id, field_of_study_id in student_program_field.get(student_id, []):
            # Add single course counts
            for course_id in course_list:
                key_self = (course_id, course_id, program_id, field_of_study_id, academic_year)
                clash_counter[key_self] += 1
                key = (course_id, program_id, field_of_study_id, academic_year)
                student_sets[key].add(student_id)

            # Course pair clashes (combinations)
            if len(course_list) >= 2:
                for courseA, courseB in combinations(sorted(course_list), 2):
                    key = (courseA, courseB, program_id, field_of_study_id, academic_year)
                    clash_counter[key] += 1

    print(f"Computed {len(clash_counter)} unique course pair clashes (with fieldOfStudyId).")
    print(f"Computed {len(student_sets)} unique course student sets (with fieldOfStudyId).")

    # --- Step 5: Import clash counts ---
    imported_clashes = 0
    for (courseA, courseB, programId, fieldOfStudyId, academicYear), count in clash_counter.items():
        clash = CourseClashCount(
            courseA=courseA,
            courseB=courseB,
            programId=programId,
            fieldOfStudyId=fieldOfStudyId,
            academicYear=academicYear,
            count=count,
            fetched_at=fetch_time
        )
        session.merge(clash)
        imported_clashes += 1

    # --- Step 6: Import student counts ---
    imported_students = 0
    for (courseOfferingId, programId, fieldOfStudyId, academicYear), student_set in student_sets.items():
        count = len(student_set)
        course_count = CourseStudentCount(
            courseOfferingId=courseOfferingId,
            programId=programId,
            fieldOfStudyId=fieldOfStudyId,
            academicYear=academicYear,
            count=count,
            fetched_at=fetch_time
        )
        session.merge(course_count)
        imported_students += 1

    session.commit()
    session.close()

    print(f"Course clash counts imported: {imported_clashes}")
    print(f"Course student counts imported: {imported_students}")

if __name__ == "__main__":
    year = 2025  # or 2026
    import_course_clash_and_student_counts_with_fields(year)
