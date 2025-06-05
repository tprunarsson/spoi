from sqlalchemy import (
    Column, String, Integer, Boolean, ForeignKey, ForeignKeyConstraint, PrimaryKeyConstraint, Text, DateTime, Float
)
from sqlalchemy.orm import declarative_base, relationship
import json
from datetime import datetime

Base = declarative_base()

# -------- Institution, Department, EducationalProgram, FieldOfStudy --------

class Institution(Base):
    __tablename__ = 'institutions'
    institutionId = Column(String, primary_key=True)
    name = Column(Text)  # JSON language map
    departments = relationship('Department', back_populates='institution')

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

    def get_name(self, lang="en"):
        data = json.loads(self.name)
        return data.get(lang, "")

class Department(Base):
    __tablename__ = 'departments'
    departmentId = Column(String, primary_key=True)
    name = Column(Text)  # JSON language map
    institutionId = Column(String, ForeignKey('institutions.institutionId'))
    institution = relationship('Institution', back_populates='departments')
    programs = relationship('EducationalProgram', back_populates='department')

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

    def get_name(self, lang="en"):
        data = json.loads(self.name)
        return data.get(lang, "")

class EducationalProgram(Base):
    __tablename__ = 'programs'
    educationalProgramId = Column(String, primary_key=True)
    name = Column(Text)
    departmentId = Column(String, ForeignKey('departments.departmentId'))
    department = relationship('Department', back_populates='programs')
    levelShort = Column(String)
    level = Column(String)
    diplomaType = Column(String)
    fields = relationship('FieldOfStudy', back_populates='program')
    shortCode = Column(String, nullable=True)      # stuttnumer
    longCode = Column(String, nullable=True)       # langtnumer
    institutionId = Column(String, ForeignKey('institutions.institutionId'))
    curriculumYear = Column(String, nullable=True) # utgafa
    programType = Column(String, nullable=True)    # gerd
    isCrossDisciplinary = Column(Boolean, nullable=True) # thverfagleg

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

    def get_name(self, lang="en"):
        data = json.loads(self.name)
        return data.get(lang, "")


class FieldOfStudy(Base):
    __tablename__ = 'fields'
    fieldOfStudyId = Column(String)
    shortName = Column(String)  
    longName = Column(Text)  
    name = Column(Text)
    educationalProgramId = Column(String, ForeignKey('programs.educationalProgramId'), nullable=True)
    program = relationship('EducationalProgram', back_populates='fields')

    __table_args__ = (
        PrimaryKeyConstraint('fieldOfStudyId', 'shortName'),
    )

    curriculumComponents = relationship('CurriculumComponent', back_populates='fieldOfStudy')

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

    def get_name(self, lang="en"):
        data = json.loads(self.name or "{}")
        return data.get(lang, "")

class CurriculumComponent(Base):
    __tablename__ = 'curriculum_components'
    curriculumComponentId = Column(Integer, primary_key=True, autoincrement=True)
    fieldOfStudyId = Column(String)
    shortName = Column(String)
    courseId = Column(String, ForeignKey('courses.courseCode'))
    courseInstanceId = Column(String, nullable=True)  # from ke_numer_array[0]
    requirementType = Column(String)
    studyYear = Column(String)
    semester = Column(String)
    curriculumYear = Column(String)
    groupName = Column(String, nullable=True)     # kjorsvid_heiti
    order = Column(Integer, nullable=True)        # rodun
    ects = Column(Float, nullable=True)           # einingar_namsleid_skref
    notes = Column(Text, nullable=True)           # skref_athugasemd
    url = Column(String, nullable=True)           # slod_namsleid_skref
    is_visible = Column(Boolean, nullable=True)   # birta[0] if present
    not_taught = Column(Boolean, nullable=True)   # ke_ekki_kennt[0] == 't'
    # Optionally, display names/descriptions (helpful for exports/search/UI):
    name_is = Column(String, nullable=True)
    name_en = Column(String, nullable=True)
    description_is = Column(Text, nullable=True)
    description_en = Column(Text, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ['fieldOfStudyId', 'shortName'],
            ['fields.fieldOfStudyId', 'fields.shortName']
        ),
    )

    fieldOfStudy = relationship('FieldOfStudy', back_populates='curriculumComponents')
    course = relationship('Course')

    @property
    def canonical_course_code(self):
        import re
        m = re.match(r"\d*([A-ZÐÞÆÖÍÁÚÉÝÓ]+[0-9]+[A-Z]*)", self.courseId)
        return m.group(1) if m else self.courseId

# -------- Course, CourseInstance, CourseComponent --------

class Course(Base):
    __tablename__ = 'courses'
    courseCode = Column(String, primary_key=True)
    canonicalName = Column(Text)  # JSON {"en": ..., "is": ...}
    # Optionally add more catalog-level info here...

    instances = relationship('CourseInstance', back_populates='course')

    def set_canonical_name(self, en, is_):
        self.canonicalName = json.dumps({"en": en, "is": is_})

    def get_canonical_name(self, lang="en"):
        data = json.loads(self.canonicalName or "{}")
        return data.get(lang, "")


# ---- CourseInstance (year/term/offering-specific) ----

class CourseInstance(Base):
    __tablename__ = 'course_instances'
    courseInstanceId = Column(String, primary_key=True)    # e.g. "09101020146"
    courseCode = Column(String, ForeignKey('courses.courseCode'))  # e.g. "STÆ101G"
    academicYear = Column(String)        # e.g. "2025"
    term = Column(String)                # e.g. "6" (map to fall/spring)
    name = Column(Text)                  # JSON {"en": ..., "is": ...}
    description = Column(Text)           # JSON {"en": ..., "is": ...}
    ects = Column(Float)                 # ECTS credits
    departmentId = Column(String, ForeignKey('departments.departmentId'))
    department = relationship('Department')
    teachers = Column(Text)              # JSON list
    languageOfInstruction = Column(String, nullable=True)
    maxStudents = Column(Integer, nullable=True)
    numberOfEnrolled = Column(Integer, nullable=True)
    timeslotPattern = Column(Text, nullable=True)           # JSON: [{type, hours, weeks}]
    roomPattern = Column(Text, nullable=True)
    numberOfTimeslots = Column(Integer, nullable=True)
    numberOfSessions = Column(Integer, nullable=True)
    weeksPattern = Column(Text, nullable=True)
    usesOverflowRooms = Column(Boolean, nullable=True)
    preferredBuildings = Column(Text, nullable=True)
    timetableHistory = Column(Text, nullable=True)
    longCourseCode = Column(String, nullable=True)          # e.g. "5055EÐL107G20156"

    # Any other data you want from source, e.g.:
    namsmat = Column(Text, nullable=True)                   # JSON or plain, grading/method
    misc = Column(Text, nullable=True)                      # Anything else for debug

    # Relationships
    course = relationship('Course', back_populates='instances')
    components = relationship('CourseComponent', back_populates='courseInstance')

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

    def set_descriptions(self, en, is_):
        self.description = json.dumps({"en": en, "is": is_})

    def get_name(self, lang="en"):
        data = json.loads(self.name or "{}")
        return data.get(lang, "")

    def get_description(self, lang="en"):
        data = json.loads(self.description or "{}")
        return data.get(lang, "")

# ---- CourseComponent (per instance, per year) ----

class CourseComponent(Base):
    __tablename__ = 'course_components'
    componentId = Column(String, primary_key=True)
    courseInstanceId = Column(String, ForeignKey('course_instances.courseInstanceId'))
    courseInstance = relationship('CourseInstance', back_populates='components')
    type = Column(String)            # e.g. "lecture", "tutorial", "lab", ...
    name = Column(Text)              # JSON language map
    teacherIds = Column(Text)        # JSON list
    roomIds = Column(Text)           # JSON list
    timeslotPattern = Column(Text)   # JSON, component-specific
    maxGroupSize = Column(Integer, nullable=True)
    usesOverflowRooms = Column(Boolean, nullable=True)

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

# -------- Room, Building, Person (Teacher/Student) --------

class Room(Base):
    __tablename__ = 'rooms'
    id = Column(Integer, primary_key=True)  # or String if room ids are not always ints
    name = Column(String)
    type = Column(String)                 # e.g., "lectureRoom"
    typeName = Column(String)             # Original "type_name"
    capacity = Column(Integer)
    examCapacity = Column(Integer)
    examSpecialCapacity = Column(Integer)
    buildingId = Column(Integer, ForeignKey('buildings.id'))
    properties = Column(Text)             # JSON for extra info

    building = relationship('Building', back_populates='rooms')

    def set_properties(self, props: dict):
        self.properties = json.dumps(props)

    def get_properties(self):
        if self.properties:
            return json.loads(self.properties)
        return {}
    
class Building(Base):
    __tablename__ = 'buildings'
    id = Column(String, primary_key=True)
    name = Column(Text)
    rooms = relationship('Room', back_populates='building')

# -------- Timetable, TimetablePlan, TimetableEvent --------

class TimetablePlan(Base):
    __tablename__ = 'timetable_plans'
    timetablePlanId = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False, default="historic")
    createdAt = Column(DateTime, nullable=False)
    description = Column(Text, nullable=True)
    sourceInfo = Column(String, nullable=True)

    events = relationship('TimetableEvent', back_populates='timetablePlan')

class TimetableEvent(Base):
    __tablename__ = 'timetable_events'
    timetableEventId = Column(Integer, primary_key=True, autoincrement=True)
    timetablePlanId = Column(String, ForeignKey('timetable_plans.timetablePlanId'), nullable=False)
    courseInstanceId = Column(String, ForeignKey('course_instances.courseInstanceId'), nullable=False)

    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    location = Column(String, nullable=True)         # Room, e.g. "VR-2 - V02-157"
    type = Column(String, nullable=True)             # Lecture, tutorial, exam, etc.
    group = Column(String, nullable=True)            # e.g. "STÆ104G-20246"
    note = Column(Text, nullable=True)               # Any remarks/notes
    teachers = Column(Text, nullable=True)           # JSON list or comma-separated

    # Relationships
    timetablePlan = relationship('TimetablePlan', back_populates='events')
    courseInstance = relationship('CourseInstance')  # Not back_populating for simplicity

    # Optional: add __repr__ for easier debugging
    def __repr__(self):
        return (f"<TimetableEvent {self.courseInstanceId} {self.start} - {self.end} "
                f"{self.location} [{self.type}]>")

class TimeBlock(Base):
    __tablename__ = 'time_blocks'
    blockId = Column(Integer, primary_key=True, autoincrement=True)
    description = Column(Text)  # Optional: e.g., "Mon 8:20-9:50 + Wed 10:00-12:20"
    intervals = relationship("BlockInterval", back_populates="block")

class BlockInterval(Base):
    __tablename__ = 'block_intervals'
    blockIntervalId = Column(Integer, primary_key=True, autoincrement=True)
    blockId = Column(Integer, ForeignKey('time_blocks.blockId'))
    day = Column(String)          # 'Monday', 'Tuesday', etc.
    start_time = Column(String)   # '08:20'
    end_time = Column(String)     # '09:50'
    block = relationship("TimeBlock", back_populates="intervals")

class CourseInstanceBlock(Base):
    __tablename__ = 'course_instance_blocks'
    id = Column(Integer, primary_key=True, autoincrement=True)
    courseInstanceId = Column(String)  # historical course instance ID (e.g., '08213020250')
    blockId = Column(Integer, ForeignKey('time_blocks.blockId'))
    roomId = Column(String, ForeignKey('rooms.id'))         # FK to Room.id (String)
    buildingId = Column(String, ForeignKey('buildings.id')) # FK to Building.id (String)
    type = Column(String, nullable=True)  # <-- NEW! e.g., "F", "D", "V", "L" (lecture, tutorial, etc.)

    block = relationship("TimeBlock")
    room = relationship("Room", foreign_keys=[roomId])
    building = relationship("Building", foreign_keys=[buildingId])

# -------- CourseInstanceTeacher, CourseStudentCount, CourseClashCount, Person --------
class CourseInstanceTeacher(Base):
    __tablename__ = 'course_instance_teachers'
    id = Column(Integer, primary_key=True, autoincrement=True)
    courseInstanceId = Column(String, ForeignKey('course_instances.courseInstanceId'))
    personId = Column(String, ForeignKey('persons.personId'))
    role = Column(String)  # e.g., "Umsjónarkennari"

    course_instance = relationship('CourseInstance')
    person = relationship('Person')

class CourseStudentCount(Base):
    __tablename__ = 'course_student_counts'
    courseInstanceId = Column(String, ForeignKey('course_instances.courseInstanceId'), primary_key=True)
    programId = Column(String, ForeignKey('programs.educationalProgramId'), primary_key=True)
    studyYear = Column(Integer, primary_key=True)
    academicYear = Column(String, primary_key=True)
    count = Column(Integer, nullable=False)

class CourseClashCount(Base):
    __tablename__ = 'course_clash_counts'
    courseA = Column(String, ForeignKey('course_instances.courseInstanceId'), primary_key=True)
    courseB = Column(String, ForeignKey('course_instances.courseInstanceId'), primary_key=True)
    programId = Column(String, ForeignKey('programs.educationalProgramId'), primary_key=True)
    studyYear = Column(Integer, primary_key=True)
    academicYear = Column(String, primary_key=True)
    count = Column(Integer, nullable=False)

class Person(Base):
    __tablename__ = 'persons'
    personId = Column(String, primary_key=True)
    name = Column(Text)              # JSON language map
    role = Column(String)            # "teacher", "student", "admin", etc.
    email = Column(String)
    phone = Column(String)

# -------- Example: OptimizationResult Table --------

class OptimizationResult(Base):
    __tablename__ = 'optimization_results'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    description = Column(Text)
    createdAt = Column(DateTime)
    parameters = Column(Text)            # JSON: solver parameters/settings
    status = Column(String)
    results = Column(Text)               # JSON: solution data, scores, etc.

# --- Relationships setup (if any back_populates needed)
EducationalProgram.fields = relationship('FieldOfStudy', back_populates='program')
FieldOfStudy.curriculumComponents = relationship('CurriculumComponent', back_populates='fieldOfStudy')
