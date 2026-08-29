package handlers

import (
	"fmt"
	"github.com/acme/svc/store"
	"github.com/gin-gonic/gin"
)

type Handler struct {
	Name string `json:"github.com/not/an/import"`
}

func Serve() { fmt.Println(store.Get()) }
