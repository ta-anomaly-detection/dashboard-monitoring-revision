package converter

import (
	"github.com/ta-anomaly-detection/dashboard-monitoring-revision/web-server/internal/domain/dto"
	"github.com/ta-anomaly-detection/dashboard-monitoring-revision/web-server/internal/domain/entity"
)

func UserToResponse(user *entity.User) *dto.UserResponse {
	return &dto.UserResponse{
		ID:        user.ID,
		Name:      user.Name,
		CreatedAt: user.CreatedAt,
		UpdatedAt: user.UpdatedAt,
	}
}

func UserToTokenResponse(user *entity.User) *dto.UserResponse {
	return &dto.UserResponse{
		Token: user.Token,
	}
}
