from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator


class TimeOfDay(StrEnum):
    EARLY_MORNING = "early_morning"
    MORNING = "morning"
    NOON = "noon"
    EVENING = "evening"
    NIGHT = "night"
    LATE_NIGHT = "late_night"


class Weather(StrEnum):
    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAIN = "rain"
    SNOW = "snow"
    WIND = "wind"


class Season(StrEnum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class Tag(StrEnum):
    NATURE = "nature"
    CITY = "city"
    TRANSIT = "transit"
    SHOP = "shop"
    WATER = "water"
    FESTIVAL = "festival"
    OTHER = "other"


TAG_CODES: dict[str, int] = {t.value: i for i, t in enumerate(Tag)}


class License(StrEnum):
    CC_BY = "cc-by"
    CC_BY_NC = "cc-by-nc"
    ALL_RIGHTS = "arr"


class Precision(StrEnum):
    EXACT = "exact"
    M100 = "100m"
    M500 = "500m"


class ReportReason(StrEnum):
    VOICE = "voice"
    PRIVATE = "private"
    COPYRIGHT = "copyright"
    OFFENSIVE = "offensive"
    SPAM = "spam"
    OTHER = "other"


JST = dt.timezone(dt.timedelta(hours=9))


def estimate_time_of_day(when: dt.datetime) -> TimeOfDay:
    h = when.astimezone(JST).hour
    if 4 <= h < 6:
        return TimeOfDay.EARLY_MORNING
    if 6 <= h < 10:
        return TimeOfDay.MORNING
    if 10 <= h < 16:
        return TimeOfDay.NOON
    if 16 <= h < 19:
        return TimeOfDay.EVENING
    if 19 <= h < 23:
        return TimeOfDay.NIGHT
    return TimeOfDay.LATE_NIGHT


def estimate_season(when: dt.datetime) -> Season:
    m = when.astimezone(JST).month
    if m in (3, 4, 5):
        return Season.SPRING
    if m in (6, 7, 8):
        return Season.SUMMER
    if m in (9, 10, 11):
        return Season.AUTUMN
    return Season.WINTER


Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class SoundMeta(BaseModel):
    title: Title
    comment: Comment = ""
    recorded_at: dt.datetime | None = None
    time_of_day: TimeOfDay | None = None
    weather: Weather | None = None
    season: Season | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=7)
    direction: int | None = Field(default=None, ge=0, le=7)
    license: License = License.CC_BY

    @field_validator("tags")
    @classmethod
    def _dedupe(cls, v: list[Tag]) -> list[Tag]:
        return list(dict.fromkeys(v))

    @field_validator("recorded_at")
    @classmethod
    def _not_future(cls, v: dt.datetime | None) -> dt.datetime | None:
        if v is None:
            return v
        if v.tzinfo is None:
            v = v.replace(tzinfo=JST)
        if v > dt.datetime.now(dt.UTC) + dt.timedelta(days=1):
            raise ValueError("recorded_at is in the future")
        return v

    def filled(self) -> SoundMeta:
        when = self.recorded_at or dt.datetime.now(dt.UTC)
        return self.model_copy(
            update={
                "time_of_day": self.time_of_day or estimate_time_of_day(when),
                "season": self.season or estimate_season(when),
            }
        )


class SoundEdit(BaseModel):
    title: Title | None = None
    comment: Comment | None = None
    recorded_at: dt.datetime | None = None
    time_of_day: TimeOfDay | None = None
    weather: Weather | None = None
    season: Season | None = None
    tags: list[Tag] | None = None
    direction: int | None = Field(default=None, ge=0, le=7)
    license: License | None = None


class ReportIn(BaseModel):
    reason: ReportReason
    detail: Comment = ""
    turnstile_token: str = Field(alias="cf-turnstile-response", default="")

    model_config = {"populate_by_name": True}


class GuessIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class LeaderboardIn(BaseModel):
    nickname: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    turnstile_token: str = Field(alias="cf-turnstile-response", default="")

    model_config = {"populate_by_name": True}


class SoundOut(BaseModel):
    id: str
    title: str
    comment: str
    recorded_at: dt.datetime | None
    time_of_day: str | None
    weather: str | None
    season: str | None
    tags: list[str]
    direction: int | None
    license: str
    lat: float
    lng: float
    precision: str
    pref_name: str | None
    city_name: str | None
    duration_sec: float | None
    webm_url: str | None
    m4a_url: str | None
    peaks_url: str | None
    play_count: int
    nearby_count: int
    published_at: dt.datetime | None
    page_url: str
