# GULLRWF2 -> Biala

from sqlalchemy.orm import sessionmaker

# from opennem.core.unit_codes import get_unit_code
# from opennem.core.unit_parser import parse_unit_duid
from opennem.db import db_connect
from opennem.db.models.opennem import Facility, Unit

engine = db_connect()
session = sessionmaker(bind=engine)


def patches() -> None:
    sqls = [
        # "update facility set capacity_registered = 2.0, unit_capacity = 2.0 where code = 'GOSNELLS'",
        # "update facility set capacity_registered = 1.1, unit_capacity = 1.1  where code = 'ATLAS'",
        # code GULLRWF2_74 -> Biala
        "update facility set active=false where network_code ='GULLRWF2'",
        "update facility set station_id = (select id from station where name = 'Wivenhoe Small Hydro') where code ='WIVENSH'",
        "update station set name = 'Wivenhoe Mini' where name = 'Wivenhoe Small'",
        # "update facility set fueltech_id = 'pumps' where network_code in ('PUMP2', 'PUMP1')",
        # "update facility set active=false where code='PIONEER'",
        # "update facility set station_id = null where name='Crookwell' and code is null",
        # "update facility set station_id = null where name='Pioneer Sugar Mill' and code is null",
    ]

    with engine.connect() as c:
        for query in sqls:
            c.execute(query)

    s = session()

    # unit = parse_unit_duid(1, duid)
    # unit_code = get_unit_code(unit, duid, "Singleton Solar Farm")

    singleton = Facility(
        name="Singleton",
        # locality="singleton",
        network_name="Singleton Solar Farm",
        # network_id="NEM",
        created_by="opennem.patches",
    )

    singleston_facility = Unit(
        code="0NSISF_1",
        status_id="operating",
        network_region="NSW1",
        network_name="Singleton Solar Farm",
        fueltech_id="solar_utility",
        # unit_id=unit.id,
        # unit_number=unit.number,
        unit_capacity=0.4,
        capacity_registered=0.4,
        created_by="opennem.patches",
    )
    singleton.units.append(singleston_facility)

    s.add(singleston_facility)
    s.add(singleton)
    s.commit()


if __name__ == "__main__":
    patches()
