"""飞牛调度器 Cron 解析"""

from apscheduler.triggers.cron import CronTrigger

from app.services.feiniu.scheduler import FeiniuScheduler


def test_default_feiniu_cron_trigger():
    s = FeiniuScheduler()
    t = s._default_feiniu_cron_trigger()
    assert isinstance(t, CronTrigger)


def test_parse_cron_invalid_falls_back_to_default():
    s = FeiniuScheduler()
    t = s._parse_cron("not-five-fields")
    assert isinstance(t, CronTrigger)


def test_parse_cron_valid_five_fields():
    s = FeiniuScheduler()
    t = s._parse_cron("0 3 * * *")
    assert isinstance(t, CronTrigger)
