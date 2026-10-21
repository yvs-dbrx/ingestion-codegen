      *================================================================*
      * COPYBOOK : PRODUCT.CPY                                         *
      * PURPOSE  : Product master with category hierarchy and pricing  *
      * RECLEN   : 233 bytes, fixed length, EBCDIC (cp037)             *
      *                                                                *
      * FIELD MAP (offset is 1-based):                                 *
      *    1-8    PROD-ID              9(8)         zoned              *
      *    9-48   PROD-NAME            X(40)        text               *
      *   49-108  PROD-DESCRIPTION     X(60)        text               *
      *  109-112  PROD-CAT-CODE        X(4)         text               *
      *  113-137  PROD-CAT-DESC        X(25)        text               *
      *  138-142  PROD-SUBCAT-CODE     X(5)         text               *
      *  143-167  PROD-SUBCAT-DESC     X(25)        text               *
      *  168-172  PROD-UNIT-PRICE      S9(7)V99     COMP-3             *
      *  173-177  PROD-UNIT-COST       S9(7)V99     COMP-3             *
      *  178-180  PROD-TAX-RATE        S9V9(4)      COMP-3             *
      *  181-184  PROD-QTY-ON-HAND     S9(7)        COMP               *
      *  185-186  PROD-REORDER-LVL     S9(4)        COMP               *
      *  187-191  PROD-WEIGHT-KG       S9(5)V9(3)   COMP-3             *
      *  192-196  PROD-UOM             X(5)         text               *
      *  197-197  PROD-STATUS          X            text               *
      *  198-205  PROD-DATE-ADDED      9(8)         zoned              *
      *  206-213  PROD-DATE-MODIFIED   9(8)         zoned              *
      *  214-233  FILLER               X(20)        text               *
      *                                                                *
      * PROD-UOM   : EACH BOX CASE PAIR SET                            *
      * PROD-STATUS: A=Active D=Discontinued S=Seasonal                *
      * PROD-TAX-RATE is a fraction: 0.0825 = 8.25 percent.            *
      *================================================================*
       01  PRODUCT-RECORD.
           05  PROD-ID               PIC 9(8).
           05  PROD-NAME             PIC X(40).
           05  PROD-DESCRIPTION      PIC X(60).
           05  PROD-CATEGORY.
               10  PROD-CAT-CODE         PIC X(4).
               10  PROD-CAT-DESC         PIC X(25).
           05  PROD-SUBCATEGORY.
               10  PROD-SUBCAT-CODE      PIC X(5).
               10  PROD-SUBCAT-DESC      PIC X(25).
           05  PROD-PRICING.
               10  PROD-UNIT-PRICE       PIC S9(7)V99 COMP-3.
               10  PROD-UNIT-COST        PIC S9(7)V99 COMP-3.
               10  PROD-TAX-RATE         PIC S9V9(4) COMP-3.
           05  PROD-INVENTORY.
               10  PROD-QTY-ON-HAND      PIC S9(7) COMP.
               10  PROD-REORDER-LVL      PIC S9(4) COMP.
               10  PROD-WEIGHT-KG        PIC S9(5)V9(3) COMP-3.
           05  PROD-UOM              PIC X(5).
           05  PROD-STATUS           PIC X.
           05  PROD-DATE-ADDED       PIC 9(8).
           05  PROD-DATE-MODIFIED    PIC 9(8).
           05  FILLER                PIC X(20).
