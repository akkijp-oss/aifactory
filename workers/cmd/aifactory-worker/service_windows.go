//go:build windows

package main

import (
	"context"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/eventlog"
	"log"
	"os"
	"time"
)

type serviceHandler struct{}

type serviceLog struct{ log *eventlog.Log }

func (l serviceLog) Write(p []byte) (int, error) {
	if err := l.log.Error(1, string(p)); err != nil {
		return 0, err
	}
	return len(p), nil
}

func (serviceHandler) Execute(_ []string, requests <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	serviceContext = ctx
	status <- svc.Status{State: svc.StartPending, WaitHint: 10000}
	done := make(chan error, 1)
	go func() { done <- run() }()
	status <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for {
		select {
		case err := <-done:
			if err != nil {
				log.Print(err)
				return true, 1
			}
			return false, 0
		case request := <-requests:
			switch request.Cmd {
			case svc.Interrogate:
				status <- request.CurrentStatus
			case svc.Stop, svc.Shutdown:
				status <- svc.Status{State: svc.StopPending, WaitHint: 45000}
				cancel()
				select {
				case err := <-done:
					if err != nil {
						return true, 1
					}
					return false, 0
				case <-time.After(45 * time.Second):
					return true, 2
				}
			}
		}
	}
}
func platformMain() bool {
	if len(os.Args) > 1 && os.Args[1] == "--job-child" {
		jobChild()
		return true
	}
	isService, err := svc.IsWindowsService()
	if err != nil {
		log.Print(err)
		os.Exit(1)
	}
	if !isService {
		return false
	}
	if events, e := eventlog.Open("AIFactoryWorker"); e == nil {
		defer events.Close()
		log.SetOutput(serviceLog{events})
	}
	if err = svc.Run("AIFactoryWorker", serviceHandler{}); err != nil {
		log.Print(err)
		os.Exit(1)
	}
	return true
}
