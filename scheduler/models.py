from __future__ import unicode_literals
import importlib
from datetime import timedelta

import croniter

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.templatetags.tz import utc
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _

import django_rq
from model_utils import Choices
from model_utils.models import TimeStampedModel


@python_2_unicode_compatible
class BaseJob(TimeStampedModel):

    name = models.CharField(_('название'), max_length=128, unique=True)
    callable = models.CharField(_('функция'), max_length=2048)
    enabled = models.BooleanField(_('активирована'), default=True)
    queue = models.CharField(_('очередь'), max_length=16)
    job_id = models.CharField(
        _('id'), max_length=128, editable=False, blank=True, null=True)
    timeout = models.IntegerField(
        _('таймаут'), blank=True, null=True,
        help_text=_(
            'Таймаут определяет максимальное время выполнения задачи,'
            'при привышении которого задача будет отменена'
        )
    )

    def __str__(self):
        return self.name

    def callable_func(self):
        path = self.callable.split('.')
        module = importlib.import_module('.'.join(path[:-1]))
        func = getattr(module, path[-1])
        if callable(func) is False:
            raise TypeError("'{}' не является callable-объектом".format(self.callable))
        return func

    def clean(self):
        self.clean_callable()
        self.clean_queue()

    def clean_callable(self):
        try:
            self.callable_func()
        except:
            raise ValidationError({
                'callable': ValidationError(
                    _('Неимпортируемый объект'), code='invalid')
            })

    def clean_queue(self):
        queue_keys = settings.RQ_QUEUES.keys()
        if self.queue not in queue_keys:
            raise ValidationError({
                'queue': ValidationError(
                    _('Неправильная очередь, выберите из: {}'.format(
                        ', '.join(queue_keys))), code='invalid')
            })

    def is_scheduled(self):
        return self.job_id in self.scheduler()
    is_scheduled.short_description = _('запланирована?')
    is_scheduled.boolean = True

    def save(self, **kwargs):
        self.unschedule()
        if self.enabled:
            self.schedule()
        super(BaseJob, self).save(**kwargs)

    def delete(self, **kwargs):
        self.unschedule()
        super(BaseJob, self).delete(**kwargs)

    def scheduler(self):
        return django_rq.get_scheduler(self.queue)

    def is_schedulable(self):
        if self.job_id:
            return False
        return self.enabled

    def schedule(self):
        if self.is_schedulable() is False:
            return False
        kwargs = {}
        if self.timeout:
            kwargs['timeout'] = self.timeout
        job = self.scheduler().enqueue_at(
            self.schedule_time_utc(), self.callable_func(),
            **kwargs
        )
        self.job_id = job.id
        return True

    def unschedule(self):
        if self.is_scheduled():
            self.scheduler().cancel(self.job_id)
        self.job_id = None
        return True

    def schedule_time_utc(self):
        return utc(self.scheduled_time)

    class Meta:
        abstract = True


class ScheduledTimeMixin(models.Model):

    scheduled_time = models.DateTimeField(_('запланированное время'))

    def schedule_time_utc(self):
        return utc(self.scheduled_time)

    class Meta:
        abstract = True


class ScheduledJob(ScheduledTimeMixin, BaseJob):

    class Meta:
        verbose_name = _('запланированная задача')
        verbose_name_plural = _('запланированные задачи')
        ordering = ('name', )


class RepeatableJob(ScheduledTimeMixin, BaseJob):

    UNITS = Choices(
        ('minutes', _('минут')),
        ('hours', _('часов')),
        ('days', _('дней')),
        ('weeks', _('недель')),
    )

    interval = models.PositiveIntegerField(_('периодичность'))
    interval_unit = models.CharField(
        _('тип значения периодичности'), max_length=12, choices=UNITS, default=UNITS.hours
    )
    repeat = models.PositiveIntegerField(_('Количество повторов'), blank=True, null=True, help_text='Оставьте поле пустым для бесконечного количества повторов')

    def interval_display(self):
        return '{} {}'.format(self.interval, self.get_interval_unit_display())

    def interval_seconds(self):
        kwargs = {
            self.interval_unit: self.interval,
        }
        return timedelta(**kwargs).total_seconds()

    def schedule(self):
        if self.is_schedulable() is False:
            return False
        kwargs = {
            'scheduled_time': self.schedule_time_utc(),
            'func': self.callable_func(),
            'interval': self.interval_seconds(),
            'repeat': self.repeat
        }
        if self.timeout:
            kwargs['timeout'] = self.timeout
        job = self.scheduler().schedule(**kwargs)
        self.job_id = job.id
        return True

    class Meta:
        verbose_name = _('повторяемая задача')
        verbose_name_plural = _('повторяемые задачи')
        ordering = ('name', )


class CronJob(BaseJob):

    cron_string = models.CharField(
        _('Cron-строка'), max_length=64,
        help_text=_('Определите периодичность в синтаксисе crontab-файла.')
    )
    repeat = models.PositiveIntegerField(_('Количество повторов'), blank=True, null=True, help_text='Оставьте поле пустым для бесконечного количества повторов')

    def clean(self):
        super(CronJob, self).clean()
        self.clean_cron_string()

    def clean_cron_string(self):
        try:
            croniter.croniter(self.cron_string)
        except ValueError as e:
            raise ValidationError({
                'cron_string': ValidationError(
                    _(str(e)), code='invalid')
            })

    def schedule(self):
        if self.is_schedulable() is False:
            return False
        kwargs = {
            'func': self.callable_func(),
            'cron_string': self.cron_string,
            'repeat': self.repeat
        }
        if self.timeout:
            kwargs['timeout'] = self.timeout
        job = self.scheduler().cron(**kwargs)
        self.job_id = job.id
        return True

    class Meta:
        verbose_name = _('cron-задача')
        verbose_name_plural = _('cron-задачи')
        ordering = ('name', )
