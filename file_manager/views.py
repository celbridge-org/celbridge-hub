from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import UploadedFile
from .serializers import UploadedFileSerializer
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from .zip_utils import extract_zip_to_public

class FileUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        print("Request data:", request.data)
        serializer = UploadedFileSerializer(data=request.data)
        if serializer.is_valid():
            instance = serializer.save()
            response_data = serializer.data

            original_name = request.FILES['file'].name
            if original_name.lower().endswith('.zip'):
                public_url = extract_zip_to_public(instance.file.path, original_name)
                response_data = dict(response_data)
                response_data['public_url'] = public_url

            return Response(response_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class FileListView(APIView):
    permission_classes = [AllowAny] 
    
    def get(self, request):
        files = UploadedFile.objects.all()
        existing = [f for f in files if f.file.storage.exists(f.file.name)]
        serializer = UploadedFileSerializer(existing, many=True)
        return Response(serializer.data)

class FileRetrieveView(APIView):
    permission_classes = [AllowAny]  
    
    def get(self, request, pk):
        uploaded_file = get_object_or_404(UploadedFile, pk=pk)
        return FileResponse(uploaded_file.file)

class FileDeleteView(APIView):
    permission_classes = [IsAuthenticated]
    
    def delete(self, request, pk):
        uploaded_file = get_object_or_404(UploadedFile, pk=pk)
        uploaded_file.file.delete()  # Delete file from storage
        uploaded_file.delete()  # Delete database record
        return Response(status=status.HTTP_204_NO_CONTENT)