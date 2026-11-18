from rest_framework import serializers


class PasswordPairSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match"}
            )
        return attrs


class RegisterSerializer(PasswordPairSerializer):
    name = serializers.CharField(max_length=128)
    email = serializers.EmailField(max_length=256)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=256)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
