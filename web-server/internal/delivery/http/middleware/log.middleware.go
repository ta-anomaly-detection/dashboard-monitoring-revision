package middleware

import (
	"fmt"
	"time"

	"github.com/labstack/echo/v4"
	"go.uber.org/zap"
)

func ZapLogger(log *zap.Logger) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			start := time.Now()

			err := next(c)
			if err != nil {
				c.Error(err)
			}

			req := c.Request()
			res := c.Response()

			// Prepare log fields as plain string
			remoteAddr := c.RealIP()
			remoteUser := "-" // if you have user auth info, put it here
			if auth := GetUser(c); auth != nil {
				remoteUser = auth.ID
			}

			timeLocal := start.Format("02/Jan/2006:15:04:05 -0700") // like [12/Jul/2025:14:33:10 +0700]
			upstreamTime := time.Since(start).Seconds()
			method := req.Method
			uri := req.RequestURI
			proto := req.Proto
			request := fmt.Sprintf("%s %s %s", method, uri, proto)
			status := res.Status
			bodyBytes := res.Size
			referer := req.Referer()
			userAgent := req.UserAgent()

			logStr := fmt.Sprintf(`%s - %s [%s] time:%.3f s "%s" %d %d "%s" "%s"`,
				remoteAddr,
				remoteUser,
				timeLocal,
				upstreamTime,
				request,
				status,
				bodyBytes,
				referer,
				userAgent,
			)

			// Log based on status code
			switch {
			case status >= 500:
				log.Error(logStr, zap.Error(err))
			case status >= 400:
				log.Warn(logStr, zap.Error(err))
			case status >= 300:
				log.Info(logStr)
			default:
				log.Info(logStr)
			}

			return nil
		}
	}
}
