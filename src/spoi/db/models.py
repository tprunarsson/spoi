from sqlalchemy import (
    Column, Table, Date, String, Integer, Boolean, ForeignKey, ForeignKeyConstraint, PrimaryKeyConstraint, Text, DateTime, Float, Index
)
from sqlalchemy.orm import declarative_base, relationship
import json
from datetime import datetime

Base = declarative_base()

def _parse_lang_json(text, lang='is'):
    import json
    try:
        return json.loads(text or '{}').get(lang, '')
    except Exception:
        return text or ''


# -------- Institution, Department, Program, FieldOfStudy --------

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
    programs = relationship('Program', back_populates='department')

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

    def get_name(self, lang="en"):
        data = json.loads(self.name)
        return data.get(lang, "")

class Program(Base):
    __tablename__ = 'programs'
    programId = Column(String, primary_key=True)
    name = Column(Text)  # JSON {'en': ..., 'is': ...}
    departmentId = Column(String, ForeignKey('departments.departmentId'))
    department = relationship('Department', back_populates='programs')
    levelShort = Column(String)
    level = Column(String)
    diplomaType = Column(String)
    fields = relationship('FieldOfStudy', back_populates='program')
    shortCode = Column(String, nullable=True)
    longCode = Column(String, nullable=True)
    institutionId = Column(String, ForeignKey('institutions.institutionId'))
    curriculumYear = Column(String, nullable=True)
    programType = Column(String, nullable=True)
    isCrossDisciplinary = Column(Boolean, nullable=True)

    offerings = relationship('ProgramOffering', back_populates='program')

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

    def get_name(self, lang="en"):
        data = json.loads(self.name or '{}')
        return data.get(lang, "")

class ProgramOffering(Base):
    __tablename__ = 'program_offerings'
    programOfferingId = Column(String, primary_key=True)  # e.g. "820193"
    programId = Column(String, ForeignKey('programs.programId'), nullable=False)
    academicYear = Column(String, nullable=False)  # e.g. "2025"
    utgafa = Column(String, nullable=True)  # e.g. "20256"
    stuttnumer = Column(String, nullable=True)
    langtnumer = Column(String, nullable=True)
    title = Column(Text)  # JSON
    degree = Column(Text)  # JSON
    ects = Column(String, nullable=True)
    namsstig = Column(String, nullable=True)
    learningOutcomes = Column(Text, nullable=True)   # JSON
    description = Column(Text, nullable=True)        # JSON
    about = Column(Text, nullable=True)              # JSON
    admission = Column(Text, nullable=True)          # JSON
    web_url = Column(Text, nullable=True)
    active = Column(Boolean, default=True)

    program = relationship('Program', back_populates='offerings')


class FieldOfStudy(Base):
    __tablename__ = 'fields_of_study'
    fieldOfStudyId = Column(String)
    shortName = Column(String)  
    longName = Column(Text)  
    name = Column(Text)
    programId = Column(String, ForeignKey('programs.programId'), nullable=True)
    program = relationship('Program', back_populates='fields')

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
    courseOfferingId = Column(String, nullable=True)
    requirementType = Column(String)
    studyYear = Column(String)
    semester = Column(String)
    curriculumYear = Column(String)
    groupName = Column(String, nullable=True)
    order = Column(Integer, nullable=True)
    ects = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    url = Column(String, nullable=True)
    is_visible = Column(Boolean, nullable=True)
    not_taught = Column(Boolean, nullable=True)
    name_is = Column(String, nullable=True)
    name_en = Column(String, nullable=True)
    description_is = Column(Text, nullable=True)
    description_en = Column(Text, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ['fieldOfStudyId', 'shortName'],
            ['fields_of_study.fieldOfStudyId', 'fields_of_study.shortName']
        ),
    )

    fieldOfStudy = relationship('FieldOfStudy', back_populates='curriculumComponents')
    course = relationship('Course')

    @property
    def canonical_course_code(self):
        import re
        m = re.match(r"\d*([A-ZÐÞÆÖÍÁÚÉÝÓ]+[0-9]+[A-Z]*)", self.courseId)
        return m.group(1) if m else self.courseId

# -------- Component (abstract), Course, CourseOffering, ComponentOffering --------

class Component(Base):
    __tablename__ = 'components'
    componentId = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)  # e.g. "Lecture", "Tutorial", "Test"
    description = Column(Text, nullable=True)
    # relationship to offerings is optional

class Course(Base):
    __tablename__ = 'courses'
    courseCode = Column(String, primary_key=True)
    canonicalName = Column(Text)  # JSON {"en": ..., "is": ...}
    offerings = relationship('CourseOffering', back_populates='course')

    def set_canonical_name(self, en, is_):
        self.canonicalName = json.dumps({"en": en, "is": is_})

    def get_canonical_name(self, lang="is"):
        return _parse_lang_json(self.canonicalName, lang)


# Association table for co-taught course instances
co_taught_instances = Table(
    'co_taught_instances',
    Base.metadata,
    Column('courseOfferingId', String, ForeignKey('course_offerings.courseOfferingId'), primary_key=True),
    Column('coTaughtWithId', String, ForeignKey('course_offerings.courseOfferingId'), primary_key=True)
)

class CourseOffering(Base):
    __tablename__ = 'course_offerings'
    courseOfferingId = Column(String, primary_key=True)
    courseCode = Column(String, ForeignKey('courses.courseCode'))
    academicYear = Column(String)
    term = Column(String)
    name = Column(Text)
    description = Column(Text)
    ects = Column(Float)
    departmentId = Column(String, ForeignKey('departments.departmentId'))
    department = relationship('Department')
    teachers = Column(Text)
    languageOfInstruction = Column(String, nullable=True)
    maxStudents = Column(Integer, nullable=True)
    numberOfEnrolled = Column(Integer, nullable=True)
    timeslotPattern = Column(Text, nullable=True)
    roomPattern = Column(Text, nullable=True)
    numberOfTimeslots = Column(Integer, nullable=True)
    numberOfSessions = Column(Integer, nullable=True)
    weeksPattern = Column(Text, nullable=True)
    usesOverflowRooms = Column(Boolean, nullable=True)
    preferredBuildings = Column(Text, nullable=True)
    timetableHistory = Column(Text, nullable=True)
    longCourseCode = Column(String, nullable=True)
    namsmat = Column(Text, nullable=True)
    misc = Column(Text, nullable=True)

    learningOutcomes = Column(Text)
    assessment = Column(Text)
    learningMaterials = Column(Text)
    prerequisites = Column(Text)
    modeOfDelivery = Column(String)
    isCoTaught = Column(Boolean)
    hasFinalExam = Column(Boolean)

    # Relationships
    course = relationship('Course', back_populates='offerings')
    componentOfferings = relationship('ComponentOffering', back_populates='courseOffering')
    events = relationship('Event', back_populates='courseOffering')

    # Co-taught (jointly delivered) course instances
    coTaughtWith = relationship(
        'CourseOffering',
        secondary=co_taught_instances,
        primaryjoin="CourseOffering.courseOfferingId == co_taught_instances.c.courseOfferingId",
        secondaryjoin="CourseOffering.courseOfferingId == co_taught_instances.c.coTaughtWithId",
        backref='coTaughtBy'
    )

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

class ComponentOffering(Base):
    __tablename__ = 'component_offerings'
    componentOfferingId = Column(String, primary_key=True)
    courseOfferingId = Column(String, ForeignKey('course_offerings.courseOfferingId'))
    componentId = Column(String, ForeignKey('components.componentId'))   # <-- Link to abstract type
    type = Column(String)            # e.g. "lecture", "tutorial", "lab", ...
    name = Column(Text)              # JSON language map
    group = Column(String, nullable=True) 
    teacherIds = Column(Text)        # JSON list
    roomIds = Column(Text)           # JSON list
    timeslotPattern = Column(Text)   # JSON, component-specific
    maxGroupSize = Column(Integer, nullable=True)
    usesOverflowRooms = Column(Boolean, nullable=True)
    # Relationships
    courseOffering = relationship('CourseOffering', back_populates='componentOfferings')
    component = relationship('Component')
    events = relationship('Event', back_populates='componentOffering')

    def set_names(self, en, is_):
        self.name = json.dumps({"en": en, "is": is_})

# -------- Room, Building, Person --------

class Room(Base):
    __tablename__ = 'rooms'
    roomId = Column(String, primary_key=True)
    name = Column(String)
    type = Column(String)
    typeName = Column(String)
    capacity = Column(Integer)
    examCapacity = Column(Integer)
    examSpecialCapacity = Column(Integer)
    buildingId = Column(String, ForeignKey('buildings.buildingId'))
    properties = Column(Text)
    building = relationship('Building', back_populates='rooms')

    def set_properties(self, props: dict):
        self.properties = json.dumps(props)

    def get_properties(self):
        if self.properties:
            return json.loads(self.properties)
        return {}
    
class Building(Base):
    __tablename__ = 'buildings'
    buildingId = Column(String, primary_key=True)
    name = Column(Text)
    rooms = relationship('Room', back_populates='building')

# -------- Scenario (Problem Definition) --------

class TimetablingScenario(Base):
    __tablename__ = "timetabling_scenarios"
    scenarioId = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    createdAt = Column(DateTime, nullable=False)
    constraints = relationship('TimetablingConstraint', back_populates='scenario')
    plans = relationship('TimetablePlan', back_populates='scenario')
    objectives = relationship('TimetablingObjective', back_populates='scenario')

class TimetablingObjective(Base):
    __tablename__ = "timetabling_objectives"
    objectiveId = Column(Integer, primary_key=True, autoincrement=True)
    scenarioId = Column(String, ForeignKey('timetabling_scenarios.scenarioId'), nullable=False)
    name = Column(String, nullable=False)
    expression = Column(Text, nullable=False)   # JSON, e.g. { "sum": ..., ... }
    description = Column(Text, nullable=True)
    priority = Column(Integer, nullable=True)   # 1=highest, etc.

    scenario = relationship('TimetablingScenario', back_populates='objectives')

class TimetablingConstraint(Base):
    __tablename__ = "timetabling_constraints"
    constraintId = Column(Integer, primary_key=True, autoincrement=True)
    scenarioId = Column(String, ForeignKey('timetabling_scenarios.scenarioId'), nullable=False)
    constraintType = Column(String, nullable=False)   # e.g., "room_capacity", "clash", "session_count"
    variableScope = Column(Text, nullable=False)      # JSON: which IDs/variables (courses, rooms, persons, etc)
    expression = Column(Text, nullable=False)         # JSON: MIP math, e.g., {"sum": [...], "leq": 3}
    is_hard = Column(Boolean, default=True)
    penalty = Column(Float, nullable=True)            # For soft constraints (objective)
    comment = Column(Text, nullable=True)
    scenario = relationship('TimetablingScenario', back_populates='constraints')

# -------- TimetablePlan (Solution) --------

class TimetablePlan(Base):
    __tablename__ = 'timetable_plans'
    timetablePlanId = Column(String, primary_key=True)
    scenarioId = Column(String, ForeignKey('timetabling_scenarios.scenarioId'), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False, default="historic")
    createdAt = Column(DateTime, nullable=False)
    description = Column(Text, nullable=True)
    sourceInfo = Column(String, nullable=True)
    scenario = relationship('TimetablingScenario', back_populates='plans')
    events = relationship('Event', back_populates='timetablePlan')

class Event(Base):
    __tablename__ = 'events'
    eventId = Column(Integer, primary_key=True, autoincrement=True)
    timetablePlanId = Column(String, ForeignKey('timetable_plans.timetablePlanId'), nullable=False)
    courseOfferingId = Column(String, ForeignKey('course_offerings.courseOfferingId'), nullable=False)
    componentOfferingId = Column(String, ForeignKey('component_offerings.componentOfferingId'), nullable=True)
    eventGroupId = Column(String, nullable=True)
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    location = Column(String, nullable=True)
    roomId = Column(String, ForeignKey('rooms.roomId'), nullable=True)
    type = Column(String, nullable=True)
    group = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    teachers = Column(Text, nullable=True)
    timetablePlan = relationship('TimetablePlan', back_populates='events')
    courseOffering = relationship('CourseOffering', back_populates='events')
    componentOffering = relationship('ComponentOffering', back_populates='events')
    room = relationship('Room')

    def __repr__(self):
        return (f"<Event {self.courseOfferingId} {self.start} - {self.end} "
                f"{self.location or self.roomId} [{self.type}]>")

# -------- TimeBlock, BlockInterval, CourseOfferingBlock --------

class TimeBlock(Base):
    __tablename__ = 'time_blocks'
    blockId = Column(Integer, primary_key=True, autoincrement=True)
    description = Column(Text)
    intervals = relationship("BlockInterval", back_populates="block")

class BlockInterval(Base):
    __tablename__ = 'block_intervals'
    blockIntervalId = Column(Integer, primary_key=True, autoincrement=True)
    blockId = Column(Integer, ForeignKey('time_blocks.blockId'))
    day = Column(String)
    start_time = Column(String)
    end_time = Column(String)
    block = relationship("TimeBlock", back_populates="intervals")

class CourseOfferingBlock(Base):
    __tablename__ = 'course_offering_blocks'
    id = Column(Integer, primary_key=True, autoincrement=True)
    courseOfferingId = Column(String)  # historical course offering ID
    blockId = Column(Integer, ForeignKey('time_blocks.blockId'))
    roomId = Column(String, ForeignKey('rooms.roomId'))
    buildingId = Column(String, ForeignKey('buildings.buildingId'))
    type = Column(String, nullable=True)
    block = relationship("TimeBlock")
    room = relationship("Room", foreign_keys=[roomId])
    building = relationship("Building", foreign_keys=[buildingId])

# -------- CourseOfferingTeacher, CourseStudentCount, CourseClashCount, Person --------

class CourseOfferingTeacher(Base):
    __tablename__ = 'course_offering_teachers'
    id = Column(Integer, primary_key=True, autoincrement=True)
    courseOfferingId = Column(String, ForeignKey('course_offerings.courseOfferingId'))
    personId = Column(String, ForeignKey('persons.personId'))
    role = Column(String)
    course_offering = relationship('CourseOffering')
    person = relationship('Person')

class CourseStudentCount(Base):
    __tablename__ = 'course_student_counts'
    courseOfferingId = Column(String, ForeignKey('course_offerings.courseOfferingId'), primary_key=True)
    programId = Column(String, ForeignKey('programs.programId'), primary_key=True)
    fieldOfStudyId = Column(String, ForeignKey('fields_of_study.fieldOfStudyId'), primary_key=True)
    academicYear = Column(String, primary_key=True)
    count = Column(Integer, nullable=False)
    fetched_at = Column(DateTime, nullable=False)

class CourseClashCount(Base):
    __tablename__ = 'course_clash_counts'
    courseA = Column(String, ForeignKey('course_offerings.courseOfferingId'), primary_key=True)
    courseB = Column(String, ForeignKey('course_offerings.courseOfferingId'), primary_key=True)
    programId = Column(String, ForeignKey('programs.programId'), primary_key=True)
    fieldOfStudyId = Column(String, ForeignKey('fields_of_study.fieldOfStudyId'), primary_key=True)
    academicYear = Column(String, primary_key=True)
    count = Column(Integer, nullable=False)
    fetched_at = Column(DateTime, nullable=False)

class Person(Base):
    __tablename__ = 'persons'
    personId = Column(String, primary_key=True)
    name = Column(Text)              # JSON language map
    role = Column(String)
    email = Column(String)
    phone = Column(String)

# -------- OptimizationResult --------

class OptimizationResult(Base):
    __tablename__ = 'optimization_results'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    description = Column(Text)
    createdAt = Column(DateTime)
    parameters = Column(Text)
    status = Column(String)
    results = Column(Text)



# --- EventMessage Model ---
class EventMessage(Base):
    __tablename__ = "event_messages"
    id = Column(Integer, primary_key=True)
    event_id = Column(String, index=True)
    timetable_version = Column(String, index=True)
    role = Column(String)  # "user" or "assistant"
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_event_version", "event_id", "timetable_version"),
    )