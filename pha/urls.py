from django.urls import path, re_path
from pha import views

urlpatterns = [
    path('', views.cong_thuc_mau, name='home'),
    path('cong-thuc-mau', views.cong_thuc_mau, name='cong_thuc_mau'),
    path('app', views.mobile, name='mobile'),
    path('pha', views.pha, name='pha'),
    path('thong-ke', views.thong_ke, name='thong_ke'),
    path('thong-ke-excel', views.export_thong_ke_excel, name='thong_ke_excel'),
    path('manifest.webmanifest', views.manifest, name='manifest'),
    path('sw.js', views.service_worker, name='sw'),
    re_path(r'^media/(?P<name>icon-(?:192|512)\.png)$', views.media_icon, name='media_icon'),
]
