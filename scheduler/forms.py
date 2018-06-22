from django.forms import ModelForm, ValidationError
from django.utils.timezone import now


class JobAdminForm(ModelForm):

    def clean_scheduled_time(self):
        data = self.cleaned_data['scheduled_time']
        if data < now():
            raise ValidationError("Запланированное время должно быть в будущем")
        return data
