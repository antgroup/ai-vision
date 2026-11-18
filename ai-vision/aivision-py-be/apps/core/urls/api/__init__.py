"""Internal and compatibility API URL registrations."""

from .biz import urlpatterns as biz_urlpatterns
from .diagnose import urlpatterns as diagnose_urlpatterns
from .traces import urlpatterns as trace_urlpatterns
from .v2 import urlpatterns as v2_urlpatterns
from .sandbox import urlpatterns as sandbox_urlpatterns

urlpatterns = [
    *biz_urlpatterns,
    *trace_urlpatterns,
    *v2_urlpatterns,
    *diagnose_urlpatterns,
    *sandbox_urlpatterns,
]

from .model_pricing import urlpatterns as model_pricing_urlpatterns
urlpatterns += model_pricing_urlpatterns
from .model_exchange_rates import urlpatterns as model_exchange_rate_urlpatterns
urlpatterns += model_exchange_rate_urlpatterns
