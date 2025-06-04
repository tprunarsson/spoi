# Spói: Scheduling Planning Optimization Interface

Spói is an interactive optimization platform for the university timetabling problem, built in Python.  

It leverages a modular, OOAPI-aligned data model to represent academic entities and schedules, and provides a foundation for both automated and human-in-the-loop timetable optimization.

## What is Spói?

Spói helps universities and colleges efficiently schedule courses, allocate rooms and timeslots, and resolve conflicts—all while supporting interactive adjustments and real-time communication with schedulers.

- **Data Model:** Structured around the [Open Education API (OOAPI) v5](https://openonderwijsapi.nl/specification/v5/) for compatibility and extensibility.
- **Optimization:** Designed for use with optimization algorithms using MIP models.
- **Interactivity:** Allows schedulers, teachers, and admins to communicate about courses and adjust schedules collaboratively.

## Features

- OOAPI-compliant, extensible class hierarchy for timetabling data
- Modular code structure for clarity and maintainability
- Support for institutions, departments, programs, fields of study, courses, course instances, rooms, people, communications, and more
- Foundation for clash detection and timetable optimization logic
- Support for interactive, chat-based timetable adjustment

---
<img src="https://github.com/tprunarsson/spoi/blob/main/images/spoi.png?raw=true" width="25%"/>
<table>
  <tr>
    <td>
      <img src="https://github.com/tprunarsson/spoi/blob/main/images/spoi.png?raw=true" width="25"/>
    </td>
    <td>
      *Spói* is named after the Icelandic word for the Whimbrel, a migratory bird celebrated for its adaptability and clarity — reflecting our mission to make academic scheduling transparent, flexible, and collaborative.
    </td>
  </tr>
</table>

---
