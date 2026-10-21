      *================================================================*
      * COPYBOOK : ORDERS.CPY                                          *
      * PURPOSE  : Order lines: one product per record, ties CUSTOMER  *
      *            to PRODUCT                                          *
      * RECLEN   : 130 bytes, fixed length, EBCDIC (cp037)             *
      *                                                                *
      * FIELD MAP (offset is 1-based):                                 *
      *    1-10   ORD-ID               9(10)        zoned              *
      *   11-18   ORD-CUST-ID          9(8)         zoned              *
      *   19-26   ORD-PROD-ID          9(8)         zoned              *
      *   27-34   ORD-ORDER-DATE       9(8)         zoned              *
      *   35-42   ORD-SHIP-DATE        9(8)         zoned              *
      *   43-50   ORD-DELIVERY-DATE    9(8)         zoned              *
      *   51-51   ORD-STATUS           X            text               *
      *   52-53   ORD-PAYMENT-METHOD   XX           text               *
      *   54-73   ORD-SHIP-CITY        X(20)        text               *
      *   74-75   ORD-SHIP-STATE       XX           text               *
      *   76-85   ORD-SHIP-ZIP         X(10)        text               *
      *   86-89   ORD-QUANTITY         S9(5)        COMP               *
      *   90-94   ORD-UNIT-PRICE       S9(7)V99     COMP-3             *
      *   95-99   ORD-DISCOUNT         S9(7)V99     COMP-3             *
      *  100-104  ORD-TAX              S9(7)V99     COMP-3             *
      *  105-108  ORD-SHIPPING         S9(5)V99     COMP-3             *
      *  109-114  ORD-TOTAL            S9(9)V99     COMP-3             *
      *  115-120  ORD-SALES-REP        X(6)         text               *
      *  121-130  FILLER               X(10)        text               *
      *                                                                *
      * ORD-STATUS : P=Pending S=Shipped D=Delivered C=Cancelled       *
      *   R=Returned                                                   *
      * ORD-PAYMENT: CC=Credit DC=Debit PP=PayPal WT=Wire CK=Check     *
      * ORD-SHIP-DATE / ORD-DELIVERY-DATE are 00000000 until they      *
      *   happen.                                                      *
      *================================================================*
       01  ORDER-RECORD.
           05  ORD-ID                PIC 9(10).
           05  ORD-CUST-ID           PIC 9(8).
           05  ORD-PROD-ID           PIC 9(8).
           05  ORD-DATES.
               10  ORD-ORDER-DATE        PIC 9(8).
               10  ORD-SHIP-DATE         PIC 9(8).
               10  ORD-DELIVERY-DATE     PIC 9(8).
           05  ORD-STATUS            PIC X.
           05  ORD-PAYMENT-METHOD    PIC XX.
           05  ORD-SHIP-TO.
               10  ORD-SHIP-CITY         PIC X(20).
               10  ORD-SHIP-STATE        PIC XX.
               10  ORD-SHIP-ZIP          PIC X(10).
           05  ORD-AMOUNTS.
               10  ORD-QUANTITY          PIC S9(5) COMP.
               10  ORD-UNIT-PRICE        PIC S9(7)V99 COMP-3.
               10  ORD-DISCOUNT          PIC S9(7)V99 COMP-3.
               10  ORD-TAX               PIC S9(7)V99 COMP-3.
               10  ORD-SHIPPING          PIC S9(5)V99 COMP-3.
               10  ORD-TOTAL             PIC S9(9)V99 COMP-3.
           05  ORD-SALES-REP         PIC X(6).
           05  FILLER                PIC X(10).
