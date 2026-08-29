package store

import "github.com/acme/svc/internal/db"

func Get() string { return db.Query() }
