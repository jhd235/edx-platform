"""Tests of openedx.features.course_duration_limits.access"""


import itertools
from datetime import datetime, timedelta

import ddt
from crum import set_current_request
from django.test import RequestFactory
from django.utils import timezone
from pytz import UTC

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.models import DynamicUpgradeDeadlineConfiguration
from openedx.core.djangoapps.schedules.tests.factories import ScheduleFactory
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from openedx.features.course_duration_limits.access import (
    generate_course_expired_message,
    get_user_course_duration,
    get_user_course_expiration_date
)
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from common.djangoapps.util.date_utils import strftime_localized


@ddt.ddt
class TestAccess(CacheIsolationTestCase):
    """Tests of openedx.features.course_duration_limits.access"""
    def setUp(self):
        super(TestAccess, self).setUp()

        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1, tzinfo=UTC))
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)

    def assertDateInMessage(self, date, message):
        # First, check that the formatted version is in there
        self.assertIn(strftime_localized(date, '%b %-d, %Y'), message)

        # But also that the machine-readable version is in there
        self.assertIn('data-datetime="%s"' % date.isoformat(), message)

    @ddt.data(
        *itertools.product(
            itertools.product([None, -2, -1, 1, 2], repeat=2),
        )
    )
    @ddt.unpack
    def test_generate_course_expired_message(self, offsets):
        now = timezone.now()
        schedule_offset, course_offset = offsets

        # Set a timezone and request, to test that the message looks at the user's setting
        request = RequestFactory().get('/')
        request.user = UserFactory()
        set_current_request(request)
        self.addCleanup(set_current_request, None)
        set_user_preference(request.user, 'time_zone', 'Asia/Tokyo')

        if schedule_offset is not None:
            schedule_upgrade_deadline = now + timedelta(days=schedule_offset)
        else:
            schedule_upgrade_deadline = None

        if course_offset is not None:
            course_upgrade_deadline = now + timedelta(days=course_offset)
        else:
            course_upgrade_deadline = None

        enrollment = CourseEnrollmentFactory.create(
            course__start=datetime(2018, 1, 1, tzinfo=UTC),
            course__self_paced=True,
        )
        CourseModeFactory.create(
            course_id=enrollment.course.id,
            mode_slug=CourseMode.VERIFIED,
            expiration_datetime=course_upgrade_deadline,
        )
        CourseModeFactory.create(
            course_id=enrollment.course.id,
            mode_slug=CourseMode.AUDIT,
        )
        ScheduleFactory.create(
            enrollment=enrollment,
            upgrade_deadline=schedule_upgrade_deadline,
        )

        duration_limit_upgrade_deadline = get_user_course_expiration_date(enrollment.user, enrollment.course)
        self.assertIsNotNone(duration_limit_upgrade_deadline)

        message = generate_course_expired_message(enrollment.user, enrollment.course)

        self.assertDateInMessage(duration_limit_upgrade_deadline, message)
        self.assertIn('data-timezone="Asia/Tokyo"', message)

        soft_upgradeable = schedule_upgrade_deadline is not None and now < schedule_upgrade_deadline
        upgradeable = course_upgrade_deadline is None or now < course_upgrade_deadline
        has_upgrade_deadline = course_upgrade_deadline is not None

        if upgradeable and soft_upgradeable:
            self.assertDateInMessage(schedule_upgrade_deadline, message)
        elif upgradeable and has_upgrade_deadline:
            self.assertDateInMessage(course_upgrade_deadline, message)
        else:
            self.assertNotIn("Upgrade by", message)

    def test_schedule_start_date_in_past(self):
        """
        Test that when schedule start date is before course start or
        enrollment date, content_availability_date is set to max of course start
        or enrollment date
        """
        enrollment = CourseEnrollmentFactory.create(
            course__start=datetime(2018, 1, 1, tzinfo=UTC),
            course__self_paced=True,
        )
        CourseModeFactory.create(
            course_id=enrollment.course.id,
            mode_slug=CourseMode.VERIFIED,
        )
        CourseModeFactory.create(
            course_id=enrollment.course.id,
            mode_slug=CourseMode.AUDIT,
        )
        ScheduleFactory.create(
            enrollment=enrollment,
            start_date=datetime(2017, 1, 1, tzinfo=UTC),
        )

        content_availability_date = max(enrollment.created, enrollment.course.start)
        access_duration = get_user_course_duration(enrollment.user, enrollment.course)
        expected_course_expiration_date = content_availability_date + access_duration

        duration_limit_upgrade_deadline = get_user_course_expiration_date(enrollment.user, enrollment.course)
        self.assertIsNotNone(duration_limit_upgrade_deadline)
        self.assertEqual(duration_limit_upgrade_deadline, expected_course_expiration_date)
