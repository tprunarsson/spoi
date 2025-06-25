from sqlalchemy_schemadisplay import create_schema_graph
from sqlalchemy import MetaData, create_engine
from spoi.db.models import Base


engine = create_engine("sqlite:///:memory:")  # in-memory dummy DB


# Optionally reflect the database (if you have an actual DB), or just use Base.metadata
graph = create_schema_graph(
    metadata=Base.metadata,
    engine=engine,
    show_datatypes=True,
    show_indexes=True,
    rankdir='LR',
    concentrate=False
)


graph.write_png('analysis/my_ooapi_timetable_erd.png')
print("Diagram saved as analyse/my_ooapi_timetable_erd.png")

