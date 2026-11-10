"""Routes of the payroll API, mounted at ``api/v1/payroll/`` by ``erp.discovery``.

Only operations documented in ``contracts/openapi/modules/payroll.yaml`` are routed.
"""

from django.urls import path

from payroll.api import views as v

urlpatterns = [
    path("periods", v.PeriodList.as_view()),
    path("periods/<uuid:pk>", v.PeriodDetail.as_view()),
    path("concepts", v.ConceptList.as_view()),
    path("concepts:validate-graph", v.ConceptValidateGraph.as_view()),
    path("concepts/<uuid:pk>", v.ConceptDetail.as_view()),
    path("novelties", v.NoveltyList.as_view()),
    path("novelties:bulk", v.NoveltyBulk.as_view()),
    path("novelties/<uuid:pk>", v.NoveltyDetail.as_view()),
    path("runs", v.RunList.as_view()),
    path("runs/<uuid:pk>", v.RunDetail.as_view()),
    path("runs/<uuid:pk>:calculate", v.RunCalculate.as_view()),
    path("runs/<uuid:pk>:approve", v.RunApprove.as_view()),
    path("runs/<uuid:pk>:post", v.RunPost.as_view()),
    path("runs/<uuid:pk>:simulate", v.RunSimulate.as_view()),
    path("runs/<uuid:pk>:pay", v.RunPay.as_view()),
    path("payslips", v.PayslipList.as_view()),
    path("payslips/<uuid:pk>", v.PayslipDetail.as_view()),
    path("electronic-payroll", v.ElectronicList.as_view()),
    path("electronic-payroll/<uuid:pk>", v.ElectronicDetail.as_view()),
    path("electronic-payroll/<uuid:pk>:generate", v.ElectronicGenerate.as_view()),
    path("electronic-payroll/<uuid:pk>:adjust", v.ElectronicAdjust.as_view()),
]
