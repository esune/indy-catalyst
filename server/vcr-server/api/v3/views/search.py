import logging

from django.conf import settings
from django.http import Http404

from rest_framework import permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import APIException
from rest_framework.mixins import ListModelMixin
from rest_framework.viewsets import ViewSetMixin

from haystack.query import RelatedSearchQuerySet

from drf_haystack.generics import HaystackGenericAPIView
from drf_haystack.filters import HaystackOrderingFilter, HaystackAutocompleteFilter
from drf_haystack.mixins import FacetMixin
from drf_haystack.viewsets import HaystackViewSet

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from api.v2.models.Credential import Credential
from api.v2.models.Name import Name
from api.v2.models.Address import Address
from api.v2.models.Topic import Topic

from api.v3.search_filters import (
    AutocompleteFilter,
    StatusFilter as AutocompleteStatusFilter,
)
from api.v3.serializers.search import (
    AggregateAutocompleteSerializer,
    TopicSearchSerializer
)


from api.v2.search.filters import (
    CategoryFilter,
    CredNameFilter,
    CustomFacetFilter,
    ExactFilter,
    StatusFilter,
)
from api.v2.serializers.search import (
    CredentialAutocompleteSerializer,
    CredentialFacetSerializer,
    CredentialSearchSerializer,
    CredentialTopicSearchSerializer,
)

from vcr_server.pagination import ResultLimitPagination

LOGGER = logging.getLogger(__name__)


class AriesHaystackViewSet(ListModelMixin, ViewSetMixin, HaystackGenericAPIView):
    """
    AriesHaystackViewSet overrides HaystackViewSet to remove the "RetrieveModelMixin".
    The HaystackViewSet class provides the default ``list()`` and
    ``retrieve()`` actions with a haystack index as it's data source.
    """
    pass


class AggregateAutocompleteView(AriesHaystackViewSet):
    """
    Return autocomplete results for a query string
    """

    permission_classes = (permissions.AllowAny,)
    pagination_class = ResultLimitPagination

    _swagger_params = [
        openapi.Parameter(
            "q", openapi.IN_QUERY, description="Query string", type=openapi.TYPE_STRING
        ),
        openapi.Parameter(
            "inactive",
            openapi.IN_QUERY,
            description="Show inactive credentials",
            type=openapi.TYPE_STRING,
            enum=["false", "true"],
            default=None,
        ),
        openapi.Parameter(
            "revoked",
            openapi.IN_QUERY,
            description="Show revoked credentials",
            type=openapi.TYPE_STRING,
            enum=["false", "true"],
            default="false",
        ),
    ]

    @swagger_auto_schema(
        manual_parameters=_swagger_params,
        responses={200: AggregateAutocompleteSerializer(many=True)},
    )
    def list(self, *args, **kwargs):
        ret = super(AggregateAutocompleteView, self).list(*args, **kwargs)
        return ret

    index_models = [Address, Name, Topic]
    load_all = True
    serializer_class = AggregateAutocompleteSerializer
    filter_backends = (AutocompleteFilter, AutocompleteStatusFilter)
    ordering = "-score"


class MissingTopicParametersException(APIException):
    status_code = 400
    default_detail = "Please provide at least a 'name' (2 characters or more) or 'topic_id'."
    default_code = "bad_request"


class TopicSearchQuerySet(RelatedSearchQuerySet):
    """
    Optimize queries when fetching topic-oriented credential search results
    """

    def __init__(self, *args, **kwargs):
        super(TopicSearchQuerySet, self).__init__(*args, **kwargs)
        self._load_all_querysets[Credential] = self.topic_queryset()

    def __len__(self):
        ret = super(TopicSearchQuerySet, self).__len__()
        if ret > LIMIT:
            ret = LIMIT
        return ret

    def topic_queryset(self):
        return Credential.objects.select_related(
            "credential_type",
            "credential_type__issuer",
            "credential_type__schema",
            "topic",
        ).all()

    def _fill_cache(self, start, end, **kwargs):
        if start is not None:
            if start > LIMIT:
                start = LIMIT
        if end is not None:
            if end > LIMIT:
                end = LIMIT
        super(TopicSearchQuerySet, self)._fill_cache(start, end, **kwargs)

    def count(self):
        ret = super(TopicSearchQuerySet, self).count()
        if ret > LIMIT:
            ret = LIMIT
        return ret


# DEPRECATED
class CredentialSearchView(AriesHaystackViewSet, FacetMixin):
    """
    Provide credential search via Solr with both faceted (/facets) and unfaceted results
    """

    permission_classes = (permissions.AllowAny,)

    _swagger_params = [
        openapi.Parameter(
            "name",
            openapi.IN_QUERY,
            description="Filter credentials by related name or topic source ID",
            type=openapi.TYPE_STRING,
        ),
        openapi.Parameter(
            "inactive",
            openapi.IN_QUERY,
            description="Show inactive credentials",
            type=openapi.TYPE_STRING,
            enum=["any", "false", "true"],
            default="false",
        ),
        openapi.Parameter(
            "latest",
            openapi.IN_QUERY,
            description="Show only latest credentials",
            type=openapi.TYPE_STRING,
            enum=["any", "false", "true"],
            default="true",
        ),
        openapi.Parameter(
            "revoked",
            openapi.IN_QUERY,
            description="Show revoked credentials",
            type=openapi.TYPE_STRING,
            enum=["any", "false", "true"],
            default="false",
        ),
        openapi.Parameter(
            "category",
            openapi.IN_QUERY,
            description="Filter by credential category. The category name and value should be joined by '::'",
            type=openapi.TYPE_STRING,
        ),
        openapi.Parameter(
            "credential_type_id",
            openapi.IN_QUERY,
            description="Filter by Credential Type ID",
            type=openapi.TYPE_STRING,
        ),
        openapi.Parameter(
            "topic_credential_type_id",
            openapi.IN_QUERY,
            description="Filter by any Credential Type ID owned by the Topic",
            type=openapi.TYPE_STRING,
        ),
        openapi.Parameter(
            "issuer_id",
            openapi.IN_QUERY,
            description="Filter by Issuer ID",
            type=openapi.TYPE_STRING,
        ),
        openapi.Parameter(
            "topic_id",
            openapi.IN_QUERY,
            description="Filter by Topic ID",
            type=openapi.TYPE_STRING,
        ),
    ]

    @swagger_auto_schema(manual_parameters=_swagger_params)
    def list(self, *args, **kwargs):
        """
        Topic search.
        Requires at minumum 'name' (2 characters or more) or 'topic_id' parameters to be supplied.
        """
        if self.object_class is TopicSearchQuerySet:
            query = self.request.GET.get("name")
            topic_id = self.request.GET.get("topic_id")
            if not self.valid_search_query(query, topic_id):
                raise MissingTopicParametersException()
        ret = super(CredentialSearchView, self).list(*args, **kwargs)
        return ret

    def valid_search_query(self, query, topic_id):
        is_valid = False
        if isinstance(query, str) and len(query.strip()) >= 2:
            is_valid = True
        if isinstance(topic_id, str) and len(topic_id.strip()) > 0:
            is_valid = True
        return is_valid

    index_models = [Credential]
    load_all = True
    serializer_class = CredentialSearchSerializer
    # enable normal filtering
    filter_backends = [
        CredNameFilter,
        CategoryFilter,
        ExactFilter,
        StatusFilter,
        HaystackOrderingFilter,
    ]
    facet_filter_backends = [
        CredNameFilter,
        ExactFilter,
        StatusFilter,
        CustomFacetFilter,
    ]
    facet_serializer_class = CredentialFacetSerializer
    facet_objects_serializer_class = CredentialSearchSerializer
    ordering_fields = ("effective_date", "revoked_date", "score")
    ordering = "-score"

    # FacetMixin provides /facets
    @action(detail=False, methods=["get"], url_path="facets")
    def facets(self, request):
        """
        We want facet_counts from the less-restricted queryset
        """
        queryset = self.get_queryset()
        facet_queryset = self.filter_facet_queryset(queryset)
        result_queryset = self.filter_queryset(queryset)

        # for facet in request.query_params.getlist(self.facet_query_params_text):
        # if ":" not in facet:
        #    continue
        # field, value = facet.split(":", 1)
        # if value:
        #    queryset = queryset.narrow('%s:"%s"' % (field, queryset.query.clean(value)))
        for key in (
            "category",
            "credential_type_id",
            "topic_credential_type_id",
            "issuer_id",
        ):
            for value in request.query_params.getlist(key):
                if value:
                    facet_queryset = facet_queryset.narrow(
                        '{}:"{}"'.format(key, queryset.query.clean(value))
                    )

        serializer = self.get_facet_serializer(
            facet_queryset.facet_counts(), objects=result_queryset, many=False
        )
        return Response(serializer.data)


LIMIT = getattr(settings, "HAYSTACK_MAX_RESULTS", 200)


# DEPRECATED:
class CredentialTopicSearchView(CredentialSearchView):
    object_class = TopicSearchQuerySet
    serializer_class = CredentialTopicSearchSerializer
    facet_objects_serializer_class = CredentialTopicSearchSerializer


class TopicSearchView(HaystackViewSet):
    """
    Provide Topic search via Solr with both faceted (/facets) and unfaceted results
    """

    permission_classes = (permissions.AllowAny,)
    
    index_models = [Topic]

    serializer_class = TopicSearchSerializer

