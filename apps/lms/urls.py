from django.urls import path
from lms import views

app_name = "lms"

urlpatterns = [
    path("",                                                    views.course_list,       name="course_list"),
    path("course/<int:course_id>/",                             views.course_detail,     name="course_detail"),
    path("enroll/<int:enrollment_id>/submit/<int:module_id>/",  views.submit_module,     name="submit_module"),
    path("review/<int:submission_id>/",                         views.review_submission, name="review_submission"),
    path("certificates/",                                       views.my_certificates,   name="certificates"),
    path("proctor/",                                            views.proctor_queue,     name="proctor_queue"),
]