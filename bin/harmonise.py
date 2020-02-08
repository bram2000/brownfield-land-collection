#!/usr/bin/env python3

#
#  normalise values by type defined in the schema
#  -- output is valid according to the 2019 guidance
#  -- log fixes as suggestions for the user to amend
#

import os
import sys
import re
import csv
import json
import validators
from datetime import datetime
from pyproj import Transformer
from decimal import Decimal

# convert from OSGB to WGS84
# https://epsg.io/27700
# https://epsg.io/4326
osgb_to_wgs84 = Transformer.from_crs(27700, 4326)

input_path = sys.argv[1]
output_path = sys.argv[2]
schema_path = sys.argv[3]
log_path = sys.argv[4]

resource = os.path.basename(os.path.splitext(input_path)[0])

schema = json.load(open(schema_path))
fields = {field["name"]: field for field in schema["fields"]}
fieldnames = [field["name"] for field in schema["fields"]]

organisation_uri = {}
default_values = {}
field_enum = {}
field_value = {}

log_fieldnames = ["row-number", "field", "datatype", "value"]
log_writer = csv.DictWriter(open(log_path, "w", newline=""), fieldnames=log_fieldnames)
log_writer.writeheader()
row_number = 0


def log_issue(field, datatype, value):
    log_writer.writerow(
        {"field": field, "datatype": datatype, "value": value, "row-number": row_number}
    )


def format_integer(value):
    return str(int(value))


def format_decimal(value, precision=6):
    return str(round(Decimal(value), precision).normalize())


def normalise_integer(field, value):
    value = normalise_integer.regex.sub("", value, 1)
    try:
        n = int(value)
    except Exception as e:
        log_issue(field, "integer", value)
        return ""
    return format_integer(n)


normalise_integer.regex = re.compile(r"\.0+$")


def normalise_decimal(field, value, precision):
    try:
        d = Decimal(value)
    except Exception as e:
        log_issue(field, "decimal", value)
        return ""
    return format_decimal(d)


def within_england(geox, geoy):
    return geoy > 49.5 and geoy < 56.0 and geox > -7.0 and geox < 2


def normalise_geometry(row):
    if row.get("GeoX", "") == "" or row.get("GeoY", "") == "":
        return row

    geox = Decimal(row["GeoX"])
    geoy = Decimal(row["GeoY"])

    row["GeoX"] = ""
    row["GeoY"] = ""

    if isinstance(geox, str) or isinstance(geoy, str):
        return row

    if within_england(geox, geoy):
        lon, lat = geox, geoy
    elif within_england(geoy, geox):
        lon, lat = geoy, geox
    else:
        lat, lon = osgb_to_wgs84.transform(geox, geoy)
        if not within_england(lon, lat):
            lat, lon = osgb_to_wgs84.transform(geoy, geox)
            if not within_england(lon, lat):
                log_issue("GeoX,GeoY", "OSGB", ",".join([row["GeoX"], row["GeoY"]]))
                return row

    row["GeoX"] = format_decimal(lon)
    row["GeoY"] = format_decimal(lat)
    return row


def lower_uri(value):
    return "".join(value.split()).lower()


def end_of_uri(value):
    return end_of_uri.regex.sub("", value.rstrip("/").lower())


end_of_uri.regex = re.compile(r".*/")


def load_organisations():
    organisation = {}
    for row in csv.DictReader(open("var/cache/organisation.csv", newline="")):
        organisation[row["organisation"]] = row
        if "opendatacommunities" in row:
            uri = row["opendatacommunities"].lower()
            organisation_uri[row["organisation"].lower()] = uri
            organisation_uri[uri] = uri
            organisation_uri[end_of_uri(uri)] = uri
            organisation_uri[row["statistical-geography"].lower()] = uri
            if "local-authority-eng" in row["organisation"]:
                dl_url = "https://digital-land.github.io/organisation/%s/" % (
                    row["organisation"]
                )
                dl_url = dl_url.lower().replace("-eng:", "-eng/")
                organisation_uri[dl_url] = uri

    for row in csv.DictReader(open("patch/organisation.csv", newline="")):
        value = lower_uri(row["value"])
        if row["organisation"]:
            organisation_uri[value] = organisation[row["organisation"]][
                "opendatacommunities"
            ]


def normalise_organisation_uri(field, fieldvalue):
    value = lower_uri(fieldvalue)

    if value in organisation_uri:
        return organisation_uri[value]

    s = end_of_uri(value)
    if s in organisation_uri:
        return organisation_uri[s]

    log_issue(field, "opendatacommunities-uri", fieldvalue)
    return ""


def normalise_date(context, fieldvalue):
    value = fieldvalue.strip(' ",')

    # all of these patterns have been used!
    for pattern in [
        "%Y-%m-%d",
        "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S.000Z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d",
        "%Y %m %d",
        "%Y.%m.%d",
        "%Y-%d-%m",  # risky!
        "%Y",
        "%Y.0",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d %B %Y",
        "%b %d, %Y",
        "%b %d, %y",
        "%b-%y",
        "%m/%d/%Y",  # risky!
    ]:
        try:
            date = datetime.strptime(value, pattern)
            return date.strftime("%Y-%m-%d")
        except ValueError:
            pass

    log_issue(field, "date", fieldvalue)
    return ""


def normalise_uri(field, value):
    # some URIs have line-breaks and spaces
    uri = "".join(value.split())

    if validators.url(uri):
        return uri

    log_issue(field, "uri", value)
    return ""


def normalise_address(field, value):
    # replace newlines and semi-colons with commas
    value = ", ".join(value.split("\n"))
    value = value.replace(";", ",")

    # normalise duplicate commas
    value = normalise_address.comma.sub(", ", value)
    value = value.strip(", ")

    # remove spaces around hyphens
    value = normalise_address.hyphen.sub("-", value)

    # remove double-quotes, normalise spaces
    value = " ".join(value.split()).replace('"', "").replace("”", "")

    return value


normalise_address.comma = re.compile(r"(\s*,\s*){1,}")
normalise_address.hyphen = re.compile(r"(\s*-\s*){1,}")


def normalise_enum_value(value):
    return " ".join(normalise_enum_value.strip.sub(" ", value.lower()).split())


normalise_enum_value.strip = re.compile(r"([^a-z0-9-_ ]+)")


def load_field_value():
    # load enum values from schema
    for field in schema["fields"]:
        fieldname = field["name"]
        if "constraints" in field and "enum" in field["constraints"]:
            field_enum.setdefault(fieldname, {})
            field_value.setdefault(fieldname, {})
            for enum in field["constraints"]["enum"]:
                value = normalise_enum_value(enum)
                field_enum[fieldname][enum] = enum
                field_value[fieldname][value] = enum

    # load fix-ups from patch file
    for row in csv.DictReader(open("patch/enum.csv", newline="")):
        fieldname = row["field"]
        enum = row["enum"]
        value = normalise_enum_value(row['value'])
        if enum not in field_enum[fieldname]:
            raise ValueError("invalid '%s' enum '%s' in patch/enum.csv" % (fieldname, enum))
        field_value.setdefault(fieldname, {})
        field_value[fieldname][value] = enum


def normalise_enum(field, fieldvalue):
    value = normalise_enum_value(fieldvalue)
    if field in field_value and value in field_value[field]:
        return field_value[field][value]

    log_issue(field, "enum", fieldvalue)
    return ""


def normalise(fieldname, value):
    if not value:
        return ""

    field = fields[fieldname]
    extra = field.get("digital-land", {})

    for strip in extra.get("strip", []):
        value = re.sub(strip, "", value)

    if fieldname == "OrganisationURI":
        return normalise_organisation_uri(fieldname, value)

    if field.get("type", "") == "integer":
        return normalise_integer(fieldname, value)

    if field.get("type", "") == "number":
        return normalise_decimal(fieldname, value, extra.get("precision", 6))

    if field.get("type", "") == "date":
        return normalise_date(fieldname, value)

    if field.get("format", "") == "uri":
        return normalise_uri(fieldname, value)

    if extra.get("format", "") == "address":
        return normalise_address(fieldname, value)

    if "enum" in field.get("constraints", {}):
        return normalise_enum(fieldname, value)

    return value


def default(o):
    for field in default_values:
        if not o[field]:
            o[field] = default_values[field]

    for field in schema["digital-land"]["duplicate"]:
        default_values[field] = o[field]

    return o


if __name__ == "__main__":
    reader = csv.DictReader(open(input_path, newline=""))

    load_organisations()
    load_field_value()

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in reader:
            row_number += 1
            o = {}
            for field in fieldnames:
                o[field] = normalise(field, row[field])

            o = normalise_geometry(o)
            o = default(o)

            writer.writerow(o)
